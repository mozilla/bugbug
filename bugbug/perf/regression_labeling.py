# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Turn raw alerts into labeled regression records.

A Perfherder alert summary names the push that moved a metric. This module
keeps the summaries that sheriffs confirmed, pins the culprit down to the
commits of one bug inside that push, decides from the regression bug whether
the regression really counts (``outcome``), and emits one record per summary.
The rules themselves live in :mod:`bugbug.perf.label_policy`.
"""

import collections
import logging
from datetime import datetime
from typing import Any, Iterable, Iterator

from bugbug.perf.label_policy import (
    CONFIRMED_BUG_RESOLUTIONS,
    CONFIRMED_SUMMARY_STATUSES,
    EXCLUDED_ALERT_STATUSES,
    EXCLUDED_FRAMEWORKS,
    EXCLUDED_SUMMARY_TAGS,
    POLICY_VERSION,
    REJECTED_BUG_RESOLUTIONS,
    RESOLVED_SUMMARY_STATUSES,
)
from bugbug.perf.task_labels import (
    get_exclusion_reason,
    get_platform_family,
    get_runnable_identity,
    normalize_task_label,
)
from bugbug.utils import parse_timestamp

logger = logging.getLogger(__name__)


def summary_is_confirmed(summary_status: str, tags: Iterable[str]) -> bool:
    """Whether sheriffs treated the summary as a real regression of the push."""
    if summary_status not in CONFIRMED_SUMMARY_STATUSES:
        return False
    return not any(
        excluded in tag for tag in tags for excluded in EXCLUDED_SUMMARY_TAGS
    )


def alert_is_positive(alert: dict[str, Any]) -> bool:
    """Whether an alert is a regression that was not dismissed or moved elsewhere."""
    return bool(alert["is_regression"]) and (
        alert["alert_status_name"] not in EXCLUDED_ALERT_STATUSES
    )


def is_within_window(
    timestamp: str | None, start: datetime | None, end: datetime
) -> bool:
    """Whether ``timestamp`` falls in ``[start, end)``; an unknown time never does."""
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return False
    if start is not None and parsed < start:
        return False
    return parsed < end


class PushIndex:
    """Pushes keyed by id, with per-repository ordering."""

    def __init__(self, pushes: Iterable[dict[str, Any]]) -> None:
        self.by_id: dict[int, dict[str, Any]] = {}
        ids: dict[str, list[int]] = collections.defaultdict(list)
        for push in pushes:
            self.by_id[push["push_id"]] = push
            ids[push["repository"]].append(push["push_id"])
        self.ids_by_repository = {
            repo: sorted(repo_ids) for repo, repo_ids in ids.items()
        }


def get_landing_id(push_id: int, commit: dict[str, Any]) -> str:
    """Identify the landing a commit belongs to: its bug within its push.

    Commits without a bug number are their own landing.
    """
    if commit["bug_id"] is not None:
        return f"{push_id}:{commit['bug_id']}"
    return f"{push_id}:{commit['revision'][:12]}"


def group_landings(push: dict[str, Any]) -> list[dict[str, Any]]:
    """Group the commits of a push into landings, in push order.

    Backouts are included and flagged: a revert is a real code change and can
    be the culprit of a regression.
    """
    grouped: dict[str, dict[str, Any]] = {}
    for commit in push["commits"]:
        key = get_landing_id(push["push_id"], commit)
        landing = grouped.setdefault(
            key,
            {
                "landing_id": key,
                "bug_id": commit["bug_id"],
                "backout": commit["backout"],
                "revisions": [],
            },
        )
        landing["revisions"].append(commit["revision"])
    return list(grouped.values())


def pin_culprit_commits(
    push: dict[str, Any] | None,
    bug: dict[str, Any] | None,
    summary_bug_number: int | None = None,
) -> tuple[list[str], str | None]:
    """Find the landing(s) of the culprit push responsible for a regression.

    Rules, in order: the regression bug's ``regressed_by`` names a bug landed
    in the push; the summary links the culprit bug itself; the push holds a
    single landing. Otherwise nothing is pinned and the push as a whole is the
    culprit.
    """
    if push is None:
        return [], None

    push_landings = group_landings(push)
    if not push_landings:
        return [], None

    def matching(bug_ids: set[int]) -> list[str]:
        return [
            revision
            for landing in push_landings
            if landing["bug_id"] in bug_ids
            for revision in landing["revisions"]
        ]

    if bug is not None and bug.get("regressed_by"):
        pinned = matching(set(bug["regressed_by"]))
        if pinned:
            return pinned, "bug"

    if summary_bug_number is not None:
        pinned = matching({summary_bug_number})
        if pinned:
            return pinned, "summary_bug"

    # A backout sharing a push with a real landing does not make it ambiguous.
    real = [landing for landing in push_landings if not landing["backout"]]
    candidates = real or push_landings
    if len(candidates) == 1:
        return list(candidates[0]["revisions"]), "single_landing"

    return [], None


def resolve_canonical_bug(
    bug: dict[str, Any] | None, bugs: dict[int, dict[str, Any]], max_depth: int = 5
) -> tuple[dict[str, Any] | None, int | None]:
    """Follow ``dupe_of`` to the bug that actually tracks the regression.

    Returns the canonical bug and its id when it differs from the linked one.
    Stops at a duplicate whose target was not fetched.
    """
    if bug is None:
        return None, None
    current = bug
    for _ in range(max_depth):
        target = current.get("dupe_of")
        if not target or target not in bugs:
            break
        current = bugs[target]
    return current, (current["id"] if current is not bug else None)


def decide_regression_outcome(
    summary_status_name: str, bug: dict[str, Any] | None, pinned_by: str | None
) -> str:
    """Decide what a confirmed summary means, using the developer's verdict.

    ``concluded``: the regression bug was resolved FIXED, WONTFIX or WORKSFORME.
    ``attributed``: the sheriff linked the culprit bug itself, or closed the
    summary without a regression bug. ``rejected``: the regression bug was
    resolved as not a regression. ``pending``: still open, or a duplicate whose
    canonical bug is unknown.
    """
    if pinned_by == "summary_bug":
        return "attributed"
    if bug is None:
        if summary_status_name in RESOLVED_SUMMARY_STATUSES:
            return "attributed"
        return "pending"
    resolution = bug.get("resolution") or ""
    if resolution in REJECTED_BUG_RESOLUTIONS:
        return "rejected"
    if resolution in CONFIRMED_BUG_RESOLUTIONS:
        return "concluded"
    if resolution != "DUPLICATE" and summary_status_name in RESOLVED_SUMMARY_STATUSES:
        return "attributed"
    return "pending"


def check_regressed_by_agreement(
    push: dict[str, Any] | None, bug: dict[str, Any] | None
) -> bool | None:
    """Whether the regression bug's ``regressed_by`` landed in the culprit push.

    None when there is nothing to compare: no push in the snapshot, no bug, or
    an empty ``regressed_by``.
    """
    if push is None or bug is None or not bug.get("regressed_by"):
        return None
    landed = {commit["bug_id"] for commit in push["commits"]}
    return bool(set(bug["regressed_by"]) & landed)


def generate_regressions(
    alerts: Iterable[dict[str, Any]],
    push_index: PushIndex,
    bugs: dict[int, dict[str, Any]],
    settle_before: datetime,
    pushed_from: datetime | None = None,
) -> Iterator[dict[str, Any]]:
    """Turn raw alerts into one labeled record per confirmed regression summary.

    Only summaries whose culprit push lies in ``[pushed_from, settle_before)``
    are labeled. Bounding on push time, as negatives are, means a push counted
    as settled never had an alert dropped for being too recent. Each record
    holds the summary, its outcome, the culprit push with its landings and
    pinned commits, and the regressed runnables; skip reasons are counted and
    logged at the end.
    """
    summaries: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for alert in alerts:
        summaries[alert["summary_id"]].append(alert)

    stats: collections.Counter = collections.Counter()
    for summary_id, summary_alerts in sorted(summaries.items()):
        head = summary_alerts[0]
        if not is_within_window(head["push_time"], pushed_from, settle_before):
            stats["outside_window"] += 1
            continue

        if not summary_is_confirmed(head["summary_status_name"], head["summary_tags"]):
            stats["not_confirmed"] += 1
            continue

        positives = [alert for alert in summary_alerts if alert_is_positive(alert)]
        if not positives:
            stats["no_regression_alerts"] += 1
            continue

        repository = head["repository"]
        push = push_index.by_id.get(head["push_id"])
        linked_bug = bugs.get(head["bug_number"]) if head["bug_number"] else None
        bug, canonical_id = resolve_canonical_bug(linked_bug, bugs)
        culprit_commits, pinned_by = pin_culprit_commits(push, bug, head["bug_number"])
        regressed_by_agrees = check_regressed_by_agreement(push, bug)
        outcome = decide_regression_outcome(head["summary_status_name"], bug, pinned_by)

        runnables = {}
        for alert in positives:
            if alert["framework"] in EXCLUDED_FRAMEWORKS:
                stats["excluded_runnables"] += 1
                continue
            for label in alert["normalized_labels"] or [None]:
                key = (alert["signature_id"], label)
                if key in runnables:
                    continue
                if label and get_exclusion_reason(
                    label, alert["framework"], alert["application"]
                ):
                    stats["excluded_runnables"] += 1
                    continue
                identity = get_runnable_identity(label) if label else None
                runnables[key] = {
                    "alert_id": alert["alert_id"],
                    "alert_status": alert["alert_status_name"],
                    "signature_id": alert["signature_id"],
                    "signature_hash": alert["signature_hash"],
                    "framework": alert["framework"],
                    "suite": alert["suite"],
                    "test": alert["test"],
                    "platform": alert["platform"],
                    "extra_options": alert["extra_options"],
                    "application": alert["application"],
                    "amount_pct": alert["amount_pct"],
                    "t_value": alert["t_value"],
                    "noise_profile": alert["noise_profile"],
                    "label": label,
                    "test_name": identity["test_name"] if identity else None,
                    "family": identity["family"] if identity else None,
                    "category": identity["category"] if identity else None,
                    "platform_family": (
                        identity["platform_family"]
                        if identity
                        else get_platform_family(alert["platform"] or "")
                    ),
                }

        if not runnables:
            stats["excluded_only"] += 1
            continue

        stats["regressions"] += 1
        stats[f"outcome_{outcome}"] += 1
        stats[f"pinned_by_{pinned_by}"] += 1
        stats[f"regressed_by_agrees_{regressed_by_agrees}"] += 1
        yield {
            "summary_id": summary_id,
            "repository": repository,
            "framework": head["framework"],
            "status": head["summary_status_name"],
            "resolved": head["summary_status_name"] in RESOLVED_SUMMARY_STATUSES,
            "summary_tags": head["summary_tags"],
            "outcome": outcome,
            "created": head["summary_created"],
            "bug_number": head["bug_number"],
            "bug_status": head["bug_status"],
            "canonical_bug": canonical_id,
            "bug_state": bug.get("status") if bug else None,
            "bug_resolution": (bug.get("resolution") or None) if bug else None,
            "push_id": head["push_id"],
            "revision": head["revision"],
            "push_time": head["push_time"],
            "reassigned": head["push_id"] != head["original_push_id"],
            "revisions": [commit["revision"] for commit in push["commits"]]
            if push
            else [],
            "landings": [
                {
                    **landing,
                    "culprit": any(r in culprit_commits for r in landing["revisions"]),
                }
                for landing in group_landings(push)
            ]
            if push
            else [],
            "culprit_commits": culprit_commits,
            "pinned_by": pinned_by,
            "regressed_by_agrees": regressed_by_agrees,
            "runnables": sorted(
                runnables.values(), key=lambda r: (r["signature_id"], r["label"] or "")
            ),
            "policy_version": POLICY_VERSION,
        }

    logger.info("Regression labeling stats: %s", dict(stats))


def get_ran_perf_labels(push: dict[str, Any]) -> set[str]:
    """Normalized labels of in-scope perf tasks that ran on a push."""
    labels = set()
    for job in push["perf_jobs"]:
        label = normalize_task_label(job["label"])
        if get_exclusion_reason(label) is None:
            labels.add(label)
    return labels
