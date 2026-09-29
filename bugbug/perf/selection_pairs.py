# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Thin test-selection datasets at commit granularity.

Positives are one row per culprit commit and regressed test identity. Every
row carries its ``get_landing_id`` (the commit's bug within its push) and
``landing_size`` so rows can be regrouped into landings or pushes, and
``push_id`` so push-level scores can be aggregated at prediction time.
Negatives are not stored; ``iter_labeled_pairs`` infers them from the clean
settled pushes in the push status dataset.
"""

import collections
from typing import Any, Iterable, Iterator

from bugbug import db
from bugbug.perf.datasets import PERF_PAIRS_DB
from bugbug.perf.label_policy import POLICY_VERSION, POSITIVE_OUTCOMES
from bugbug.perf.push_status import (
    get_push_commits,
    iter_clean_commits,
    read_push_status,
)
from bugbug.perf.regression_labeling import PushIndex


def generate_scheduling_pairs(
    regressions: Iterable[dict[str, Any]], push_index: PushIndex, repository: str
) -> Iterator[dict[str, Any]]:
    """One row per (culprit commit, test identity), merging platforms and summaries.

    Pinned regressions label their culprit commits; unpinned ones label every
    commit of the push, with ``pinned_by`` null so consumers can filter them
    out.
    """
    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for regression in regressions:
        if regression["repository"] != repository:
            continue
        if regression["outcome"] not in POSITIVE_OUTCOMES:
            continue
        push = push_index.by_id.get(regression["push_id"])
        if push is None:
            continue
        commits = get_push_commits(push)
        if regression["culprit_commits"]:
            culprits = set(regression["culprit_commits"])
            commits = [c for c in commits if c["node"] in culprits]

        for commit in commits:
            for runnable in regression["runnables"]:
                if not runnable["label"]:
                    continue
                key = (commit["node"], runnable["test_name"])
                pair = pairs.get(key)
                if pair is None:
                    pair = pairs[key] = {
                        "repository": repository,
                        "push_id": push["push_id"],
                        "revision": push["revision"],
                        "push_time": push["time"],
                        "node": commit["node"],
                        "bug_id": commit["bug_id"],
                        "backout": commit["backout"],
                        "landing_id": commit["landing_id"],
                        "landing_size": commit["landing_size"],
                        "pinned_by": regression["pinned_by"],
                        "test_name": runnable["test_name"],
                        "family": runnable["family"],
                        "category": runnable["category"],
                        "framework": runnable["framework"],
                        "application": runnable["application"],
                        "label": 1,
                        "platform_families": set(),
                        "platform_labels": set(),
                        "summary_ids": set(),
                        "policy_version": POLICY_VERSION,
                    }
                pair["platform_families"].add(runnable["platform_family"])
                pair["platform_labels"].add(runnable["label"])
                pair["summary_ids"].add(regression["summary_id"])

    for key in sorted(pairs):
        pair = pairs[key]
        for field in ("platform_families", "platform_labels", "summary_ids"):
            pair[field] = sorted(pair[field])
        yield pair


def iter_labeled_pairs(
    candidates: set[str] | None = None, repository: str = "autoland"
) -> Iterator[dict[str, Any]]:
    """Yield positive pairs and inferred negatives at commit granularity.

    Negatives are every candidate identity for every commit from
    :func:`bugbug.perf.push_status.iter_clean_commits`; pending and rejected pushes
    yield nothing. ``candidates`` defaults to every identity that ran.
    """
    statuses = read_push_status(repository)
    if candidates is None:
        candidates = {
            identity for row in statuses for identity in row["identities_ran"]
        }

    positives: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for pair in db.read(PERF_PAIRS_DB):
        if pair["repository"] == repository:
            positives[pair["push_id"]].append(pair)

    for status in statuses:
        if status["push_id"] in positives:
            yield from positives[status["push_id"]]
            continue
        for _, commit in iter_clean_commits([status]):
            for identity in sorted(candidates):
                yield {
                    "repository": repository,
                    "push_id": status["push_id"],
                    "revision": status["revision"],
                    "push_time": status["push_time"],
                    "node": commit["node"],
                    "bug_id": commit["bug_id"],
                    "backout": False,
                    "landing_id": commit["landing_id"],
                    "landing_size": commit["landing_size"],
                    "pinned_by": None,
                    "test_name": identity,
                    "label": 0,
                    "policy_version": POLICY_VERSION,
                }
