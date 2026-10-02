# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Per-push label state, the shared source of negatives.

``generate_push_status`` writes one row per push saying whether it is a
culprit, pending, rejected or clean, whether it is settled, and which commits
and test identities it holds. Both model datasets take their clean commits
from that file through ``iter_clean_commits``, so the rule for what counts as a
negative lives here and nowhere else.
"""

import collections
from datetime import datetime
from typing import Any, Iterable, Iterator

from bugbug import db
from bugbug.perf.datasets import PERF_PUSH_STATUS_DB
from bugbug.perf.label_policy import POLICY_VERSION, POSITIVE_OUTCOMES
from bugbug.perf.regression_labeling import (
    PushIndex,
    get_landing_id,
    get_ran_perf_labels,
    is_within_window,
)
from bugbug.perf.task_labels import get_runnable_identity


def get_push_commits(push: dict[str, Any]) -> list[dict[str, Any]]:
    """Commits of a push with their landing id and landing size.

    Backouts are included and flagged so a revert can be labeled as a culprit;
    negative sampling skips them.
    """
    sizes: collections.Counter = collections.Counter(
        get_landing_id(push["push_id"], c) for c in push["commits"]
    )
    return [
        {
            "node": commit["revision"],
            "bug_id": commit["bug_id"],
            "backout": commit["backout"],
            "landing_id": get_landing_id(push["push_id"], commit),
            "landing_size": sizes[get_landing_id(push["push_id"], commit)],
        }
        for commit in push["commits"]
    ]


def get_push_label_states(
    regressions: Iterable[dict[str, Any]], repository: str
) -> dict[int, str]:
    """Classify every push that has a regression: culprit, pending or rejected.

    A push with at least one positive regression is a culprit. Otherwise it is
    pending if any of its regressions is still undecided, else rejected. Pushes
    absent from the result are clean.
    """
    rank = {"rejected": 0, "pending": 1, "culprit": 2}
    states: dict[int, str] = {}
    for regression in regressions:
        if regression["repository"] != repository:
            continue
        if regression["outcome"] in POSITIVE_OUTCOMES:
            state = "culprit"
        elif regression["outcome"] == "pending":
            state = "pending"
        else:
            state = "rejected"
        current = states.get(regression["push_id"])
        if current is None or rank[state] > rank[current]:
            states[regression["push_id"]] = state
    return states


def is_within_labeled_window(
    push: dict[str, Any], labeled_from: datetime | None, settle_before: datetime
) -> bool:
    """Whether every alert the push could have raised is in the snapshot and settled.

    Pushes before ``labeled_from`` predate the alerts window: their alerts were
    never fetched, so they only look clean. Pushes after ``settle_before`` are
    still being triaged.
    """
    return is_within_window(push["time"], labeled_from, settle_before)


def generate_push_status(
    push_index: PushIndex,
    regressions: Iterable[dict[str, Any]],
    repository: str,
    settle_before: datetime,
    labeled_from: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    """One row per push: its label state, whether it is settled, what ran, commits.

    ``label_state`` is ``culprit``, ``pending``, ``rejected`` or ``clean``. Only
    settled clean pushes are negatives, for every test identity and each of
    their non-backout commits. ``settled`` is false for pushes older than the
    alerts window, whose alerts are not in the snapshot.
    """
    states = get_push_label_states(regressions, repository)
    for push_id in push_index.ids_by_repository.get(repository, []):
        push = push_index.by_id[push_id]
        labels = get_ran_perf_labels(push)
        yield {
            "repository": repository,
            "push_id": push_id,
            "revision": push["revision"],
            "push_time": push["time"],
            "label_state": states.get(push_id, "clean"),
            "settled": is_within_labeled_window(push, labeled_from, settle_before),
            "commits": get_push_commits(push),
            "identities_ran": sorted(
                {get_runnable_identity(label)["test_name"] for label in labels}
            ),
            "platform_families_ran": sorted(
                {get_runnable_identity(label)["platform_family"] for label in labels}
            ),
            "policy_version": POLICY_VERSION,
        }


def read_push_status(repository: str | None = None) -> list[dict[str, Any]]:
    """Load the stored push status rows, optionally for one repository."""
    return [
        row
        for row in db.read(PERF_PUSH_STATUS_DB)
        if repository is None or row["repository"] == repository
    ]


def iter_clean_commits(
    statuses: Iterable[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield ``(status, commit)`` for each non-backout commit of a settled clean push.

    These are the negative candidates shared by both model datasets.
    """
    for status in statuses:
        if not status["settled"] or status["label_state"] != "clean":
            continue
        for commit in status["commits"]:
            if not commit["backout"]:
                yield status, commit
