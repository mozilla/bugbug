# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Commit-level dataset for the performance regression predictor.

Builds the training set for the transformer model, which reads a commit's
message and diff. ``select_export_commits`` picks culprit commits and sampled
clean commits, ``assign_train_test_splits`` holds out the newest rows as the test set and
``attach_commit_patches`` reads the patch text from a local Mercurial clone. The
``export`` command of ``scripts/perf_alerts_retriever.py`` runs the three.
"""

import logging
import random
from typing import Any, Iterable, Iterator

import hglib

from bugbug.perf.label_policy import POLICY_VERSION, POSITIVE_OUTCOMES
from bugbug.perf.push_status import iter_clean_commits

logger = logging.getLogger(__name__)


def export_hg_patches(repo_dir: str, revs: list[bytes]) -> list[bytes]:
    """Run ``hg export`` in git style for each revision within one client session."""
    with hglib.open(repo_dir) as hg:
        return [hg.export(revs=[rev], git=True) for rev in revs]


def parse_hg_export(patch: bytes) -> tuple[str, str]:
    """Split an ``hg export`` payload into commit message and diff.

    The ``# ...`` header lines are dropped. The diff starts at the first
    ``diff `` line and is empty for a commit with no file changes.
    """
    text = patch.decode("utf-8", "replace")
    message_lines: list[str] = []
    offset = 0
    diff_start = len(text)
    for line in text.splitlines(keepends=True):
        if line.startswith("diff "):
            diff_start = offset
            break
        offset += len(line)
        if line.startswith("# "):
            continue
        message_lines.append(line)
    return "".join(message_lines).strip(), text[diff_start:]


def select_export_commits(
    regressions: Iterable[dict[str, Any]],
    statuses: Iterable[dict[str, Any]],
    negative_ratio: float = 5.0,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """Choose positive and sampled negative commits for the transformer dataset.

    Positives are the ``culprit_commits`` of regressions with a positive
    outcome, or every commit of the push when none was pinned; commit fields
    come from the push status rows. Negatives are drawn from
    :func:`bugbug.perf.push_status.iter_clean_commits`, ``negative_ratio`` times the
    number of positives, using ``seed`` over a sorted candidate list so the
    sample does not depend on file order.
    """
    statuses = list(statuses)
    by_push = {status["push_id"]: status for status in statuses}

    positives: dict[str, dict[str, Any]] = {}
    for regression in regressions:
        if regression["outcome"] not in POSITIVE_OUTCOMES:
            continue
        status = by_push.get(regression["push_id"])
        if status is None:
            continue
        commits = status["commits"]
        if regression["culprit_commits"]:
            culprits = set(regression["culprit_commits"])
            commits = [c for c in commits if c["node"] in culprits]

        for commit in commits:
            record = positives.get(commit["node"])
            if record is None:
                record = positives[commit["node"]] = {
                    **commit,
                    "repository": status["repository"],
                    "push_id": status["push_id"],
                    "pushdate": status["push_time"],
                    "label": 1,
                    "pinned_by": regression["pinned_by"],
                    "summary_ids": [],
                }
            record["summary_ids"].append(regression["summary_id"])

    candidates = sorted(
        iter_clean_commits(statuses), key=lambda sc: (sc[0]["push_time"], sc[1]["node"])
    )
    rng = random.Random(seed)
    sample_size = min(len(candidates), int(len(positives) * negative_ratio))
    negatives = [
        {
            **commit,
            "repository": status["repository"],
            "push_id": status["push_id"],
            "pushdate": status["push_time"],
            "label": 0,
            "pinned_by": None,
            "summary_ids": [],
        }
        for status, commit in rng.sample(candidates, sample_size)
    ]

    selected = list(positives.values()) + negatives
    selected.sort(key=lambda record: (record["pushdate"], record["node"]))
    logger.info(
        "Selected %d positive and %d negative commits for export",
        len(positives),
        len(negatives),
    )
    return selected


def assign_train_test_splits(
    records: list[dict[str, Any]], test_fraction: float
) -> None:
    """Mark the newest ``test_fraction`` of records, by push date, as ``test``.

    A time split mirrors production, where the model scores pushes newer than
    anything it was trained on.
    """
    records.sort(key=lambda record: (record["pushdate"], record["node"]))
    cutoff = int(len(records) * (1 - test_fraction))
    for i, record in enumerate(records):
        record["split"] = "train" if i < cutoff else "test"


def attach_commit_patches(
    records: list[dict[str, Any]], repo_dir: str, chunk_size: int = 200
) -> Iterator[dict[str, Any]]:
    """Attach commit message and diff from the local clone to each record.

    Commits are exported ``chunk_size`` at a time. A failing chunk is retried
    one commit at a time, and revisions missing from the clone are dropped
    with a warning.
    """
    missing = 0
    for start in range(0, len(records), chunk_size):
        chunk = records[start : start + chunk_size]
        revs = [record["node"].encode("ascii") for record in chunk]
        patches: list[bytes | None]
        try:
            patches = list(export_hg_patches(repo_dir, revs))
        except hglib.error.CommandError:
            patches = []
            for rev in revs:
                try:
                    patches.extend(export_hg_patches(repo_dir, [rev]))
                except hglib.error.CommandError:
                    patches.append(None)

        for record, patch in zip(chunk, patches):
            if patch is None:
                missing += 1
                continue
            message, diff = parse_hg_export(patch)
            yield {
                **record,
                "commit_message": message,
                "diff": diff,
                "policy_version": POLICY_VERSION,
            }

    if missing:
        logger.warning("%d commits were not found in %s", missing, repo_dir)
