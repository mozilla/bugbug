# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The history a scheduling strategy is replayed over.

A :class:`Timeline` is the ordered list of autoland pushes from
``perf_pushes.json``, the confirmed regressions of ``perf_regressions.json``
pinned to their culprit push, and, for the risk-aware strategies, the risk
score of every commit from ``perf_commit_risk.json``. It also knows which
performance tasks existed at any point: the labels that ran on the nearest
push where the whole suite ran, since the task graph itself is not archived.
"""

import collections
import logging
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Iterable, Mapping

from bugbug.perf.calibration import Calibration
from bugbug.perf.label_policy import POSITIVE_OUTCOMES
from bugbug.perf.task_labels import get_exclusion_reason, normalize_task_label
from bugbug.utils import parse_timestamp

logger = logging.getLogger(__name__)

# A push where at least this many distinct perf labels ran carried the whole
# suite (a backstop push); sheriff backfills never come close.
FULL_SUITE_MIN_LABELS = 300
# The decision task strips every task from a push whose commit message says so.
DONTBUILD_MARKER = "DONTBUILD"


@dataclass(frozen=True)
class Commit:
    node: str
    backout: bool = False
    risk: float | None = None
    ignored_reason: str | None = None

    @property
    def scored(self) -> bool:
        """Whether the predictor scored the commit rather than skipping it."""
        return self.ignored_reason is None


@dataclass
class Push:
    index: int
    push_id: int
    pushlog_id: int | None
    time: datetime
    commits: tuple[Commit, ...]
    dontbuild: bool
    ran_labels: frozenset[str] = field(default_factory=frozenset)

    @property
    def full_suite(self) -> bool:
        return len(self.ran_labels) >= FULL_SUITE_MIN_LABELS


@dataclass(frozen=True)
class Regression:
    """A confirmed regression summary pinned to its culprit push.

    ``labels`` are the normalized task labels whose series regressed; empty
    when no alert of the summary could be mapped to a task, in which case any
    full run of the suite is taken to cover it.
    """

    summary_id: int
    push_index: int
    time: datetime
    labels: frozenset[str] = field(default_factory=frozenset)


class Timeline:
    def __init__(self, pushes: list[Push], regressions: list[Regression]) -> None:
        self.pushes = pushes
        self.regressions = sorted(
            regressions, key=lambda r: (r.push_index, r.summary_id)
        )
        self.by_push_id = {push.push_id: push for push in pushes}
        self._suite_source = self._suite_sources(pushes)

    @staticmethod
    def _suite_sources(pushes: list[Push]) -> list[int]:
        """For each push, the index of the full-suite push describing the suite then.

        The nearest full-suite push at or before it, else the first one after
        it, else -1 when no push in the history ran the whole suite.
        """
        sources = [-1] * len(pushes)
        last = -1
        for push in pushes:
            if push.full_suite:
                last = push.index
            sources[push.index] = last
        following = -1
        for push in reversed(pushes):
            if push.full_suite:
                following = push.index
            if sources[push.index] == -1:
                sources[push.index] = following
        return sources

    def with_calibration(self, calibration: Calibration) -> "Timeline":
        """A copy whose commit risks are mapped through ``calibration``."""
        pushes = [
            replace(
                push,
                commits=tuple(
                    replace(commit, risk=calibration.apply(commit.risk))
                    if commit.risk is not None
                    else commit
                    for commit in push.commits
                ),
            )
            for push in self.pushes
        ]
        return Timeline(pushes, self.regressions)

    @property
    def has_pushlog_ids(self) -> bool:
        return all(push.pushlog_id is not None for push in self.pushes)

    @property
    def start(self) -> datetime:
        return self.pushes[0].time

    @property
    def end(self) -> datetime:
        return self.pushes[-1].time

    def suite_at(self, index: int) -> frozenset[str]:
        """The perf labels that existed, and so would run in a batch, at ``index``."""
        source = self._suite_source[index]
        return self.pushes[source].ran_labels if source >= 0 else frozenset()

    def hours(self, start_index: int, end_index: int) -> float:
        return (
            self.pushes[end_index].time - self.pushes[start_index].time
        ).total_seconds() / 3600

    def unscored_commits(self) -> list[Commit]:
        """Commits the predictor would score but that carry no risk score."""
        return [
            commit
            for push in self.pushes
            for commit in push.commits
            if commit.scored and commit.risk is None
        ]

    def expanded_labels(
        self, interval: int = 10, min_share: float = 0.5
    ) -> frozenset[str]:
        """Labels production runs on every ``interval``-th push besides backstops.

        Derived from history: the labels that ran on at least ``min_share`` of
        the pushes whose pushlog id is a multiple of ``interval`` and that did
        not carry the whole suite. Empty without pushlog ids.
        """
        if not self.has_pushlog_ids:
            return frozenset()
        counts: collections.Counter = collections.Counter()
        candidates = 0
        for push in self.pushes:
            if push.full_suite or push.dontbuild or push.pushlog_id % interval:  # type: ignore[operator]
                continue
            candidates += 1
            counts.update(push.ran_labels)
        if not candidates:
            return frozenset()
        return frozenset(
            label for label, count in counts.items() if count >= min_share * candidates
        )

    @classmethod
    def from_records(
        cls,
        pushes: Iterable[dict[str, Any]],
        regressions: Iterable[dict[str, Any]] = (),
        risks: Mapping[str, tuple[float | None, str | None]] | None = None,
    ) -> "Timeline":
        """Build a timeline from dataset records.

        ``pushes`` and ``regressions`` are ``perf_pushes.json`` and
        ``perf_regressions.json`` records; only regressions with a positive
        outcome whose culprit push is present are kept. ``risks`` maps a commit
        node to its ``(risk_score, ignored_reason)`` from ``perf_commit_risk.json``.
        """
        parsed = []
        for record in pushes:
            time = parse_timestamp(record["time"])
            if time is None:
                continue
            parsed.append((time, record["push_id"], record))
        parsed.sort(key=lambda item: (item[0], item[1]))

        built: list[Push] = []
        for index, (time, push_id, record) in enumerate(parsed):
            commits = []
            for commit in record["commits"]:
                risk, reason = (None, None)
                if risks is not None:
                    risk, reason = risks.get(commit["revision"], (None, None))
                commits.append(
                    Commit(
                        sys.intern(commit["revision"]),
                        bool(commit.get("backout")),
                        risk,
                        reason,
                    )
                )
            labels = set()
            for job in record.get("perf_jobs", []):
                label = normalize_task_label(job["label"])
                if get_exclusion_reason(label) is None:
                    labels.add(sys.intern(label))
            built.append(
                Push(
                    index,
                    push_id,
                    record.get("pushlog_id"),
                    time,
                    tuple(commits),
                    any(
                        DONTBUILD_MARKER in (c.get("desc") or "")
                        for c in record["commits"]
                    ),
                    frozenset(labels),
                )
            )
        by_push_id = {push.push_id: push for push in built}

        kept: list[Regression] = []
        skipped = 0
        for record in regressions:
            if record["outcome"] not in POSITIVE_OUTCOMES:
                continue
            push = by_push_id.get(record["push_id"])
            if push is None:
                skipped += 1
                continue
            kept.append(
                Regression(
                    record["summary_id"],
                    push.index,
                    push.time,
                    frozenset(
                        sys.intern(runnable["label"])
                        for runnable in record["runnables"]
                        if runnable.get("label")
                    ),
                )
            )
        if skipped:
            logger.warning(
                "%d regressions dropped: their culprit push is not in the pushes",
                skipped,
            )
        logger.info(
            "Timeline: %d pushes from %s to %s, %d full-suite pushes, %d regressions",
            len(built),
            built[0].time if built else None,
            built[-1].time if built else None,
            sum(push.full_suite for push in built),
            len(kept),
        )
        return cls(built, kept)


def load_risks(
    records: Iterable[dict[str, Any]], model_version: str | None = None
) -> tuple[dict[str, tuple[float | None, str | None]], str | None]:
    """Index ``perf_commit_risk.json`` records by node for one model version.

    Without ``model_version`` the records must all come from the same model.
    Returns the mapping and the version used.
    """
    versions: set[str] = set()
    risks: dict[str, tuple[float | None, str | None]] = {}
    for record in records:
        if model_version is not None and record["model_version"] != model_version:
            continue
        versions.add(record["model_version"])
        risks[record["node"]] = (record["risk_score"], record["ignored_reason"])
    if len(versions) > 1:
        raise ValueError(
            f"Risk scores from several model versions {sorted(versions)}; pass one"
        )
    return risks, next(iter(versions), model_version)
