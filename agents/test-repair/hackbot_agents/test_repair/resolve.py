# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Resolve a failing Taskcluster test task into everything the agent needs.

From a task id alone: the push it belongs to, the tests that failed, the revision
those tests were last green at on the same platform, the git range that landed
since, and the intermittent bugs Treeherder ties to the failure. Only the range
endpoints are mapped to git; the agent enumerates the commits between them with
``git log`` in the checkout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import mozci.push  # noqa: F401  (imported so mozci registers its data sources)
import requests
from mozci import data
from mozci.task import is_no_groups_suite

logger = logging.getLogger(__name__)

_TC_TASK_URL = "https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/{task_id}"
_LANDO_HG2GIT = "https://lando.moz.tools/api/hg2git/firefox/{rev}"
_HG_BASE = "https://hg.mozilla.org"
# Taskcluster ``project`` tag -> hg pushlog repository path.
_REPO_PATHS = {
    "autoland": "integration/autoland",
    "mozilla-central": "mozilla-central",
    "mozilla-beta": "releases/mozilla-beta",
    "mozilla-release": "releases/mozilla-release",
    "try": "try",
}
_HEADERS = {"User-Agent": "hackbot-test-repair/1.0"}
_TIMEOUT = 30
_TREEHERDER = "https://treeherder.mozilla.org/api/project"
_FAILURE_LINE_PREFIX = "TEST-UNEXPECTED"
_INTERMITTENT_KEYWORD = "intermittent-failure"
# How far back the candidate window reaches. The agent narrows it itself with
# `treeherder-cli --lookback N --suspects`, which reports the push a failure actually
# started in -- including when that predates the push under investigation.
RANGE_PUSHES = 100
# Bounds the shallow clone depth, and so the commits the agent can reach.
MAX_RANGE_COMMITS = 500


@dataclass(frozen=True)
class FailingGroup:
    """A failing test manifest and every test that failed in it."""

    group: str
    tests: list[str]


@dataclass(frozen=True)
class CommitRange:
    """The commits to search for the culprit: ``span`` back from ``head``."""

    head: str
    span: int


@dataclass
class Investigation:
    """The resolved context for one test-repair run, derived from a task id."""

    project: str
    hg_revision: str
    harness: str
    # Taskcluster ``test-platform`` tag, e.g. "linux1804-64-qr/debug".
    platform: str
    failing_groups: list[FailingGroup]
    commit_range: CommitRange
    # Carries the test variant and chunk, unlike ``platform``.
    label: str = ""
    # False for suites that report no test manifests (gtest, jittest, talos, ...).
    group_based: bool = True
    known_intermittent_bugs: list[int] = field(default_factory=list)

    @property
    def failure_commit(self) -> str:
        return self.commit_range.head

    @property
    def debug_build(self) -> bool:
        return "debug" in self.platform

    @property
    def is_linux(self) -> bool:
        """Whether the agent's Linux container is the same OS family."""
        return self.platform.startswith("linux")

    @property
    def sanitizer(self) -> str | None:
        """The sanitizer CI built with, if any."""
        for name in ("asan", "tsan"):
            if f"-{name}" in self.platform:
                return name
        return None

    @property
    def coverage_build(self) -> bool:
        return "-ccov" in self.platform


def _get_json(url: str) -> dict:
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _hg_to_git(rev: str) -> str | None:
    try:
        return _get_json(_LANDO_HG2GIT.format(rev=rev)).get("git_hash")
    except requests.exceptions.RequestException:
        logger.warning("lando hg2git lookup failed for %s", rev)
        return None


def _harness(tags: dict) -> str:
    """The tests.firefox.dev harness key for a test task."""
    suite = tags.get("test-suite") or ""
    label = tags.get("label") or ""
    if "xpcshell" in suite or "xpcshell" in label:
        return "xpcshell"
    if "mochitest" in suite:
        return "mochitest"
    # ``kind`` is "test" for every Firefox test task, so it is a last resort.
    return suite or tags.get("kind") or "unknown"


def _failing_groups(task_id: str) -> list[FailingGroup]:
    """Failing test groups for a task, via mozci; empty on any error."""
    try:
        by_group = data.handler.get("test_task_failure_types", task_id=task_id)
    except Exception:
        logger.exception("Could not read failing groups for task %s", task_id)
        return []
    groups: list[FailingGroup] = []
    for group, fails in by_group.items():
        if not group or not fails:
            continue
        groups.append(FailingGroup(group=group, tests=[test for test, _type in fails]))
    return groups


# Treeherder /api/failureclassification/, restricted to the verdicts that mean a
# sheriff has already dealt with the failure. "not classified" (1) and "new failure
# not classified" (6) are left out: they still may be a real regression.
_ACTIONED_CLASSIFICATIONS = {
    2: "fixed by commit",
    3: "expected fail",
    4: "intermittent",
    5: "infra",
    7: "autoclassified intermittent",
    8: "intermittent needs bugid",
}


def sheriff_classification(project: str, task_id: str) -> str | None:
    """How a sheriff classified this failure while the run worked, if they did.

    A run takes long enough that the tree is often dealt with before it reports.
    Best effort: None on any error, so the report goes out unmarked rather than
    not at all.
    """
    try:
        jobs = (
            _get_json(f"{_TREEHERDER}/{project}/jobs/?task_id={task_id}").get("results")
            or []
        )
    except (requests.exceptions.RequestException, ValueError):
        logger.warning("Could not re-read the classification of task %s", task_id)
        return None
    if not jobs:
        return None
    return _ACTIONED_CLASSIFICATIONS.get(jobs[0].get("failure_classification_id"))


def _open_intermittent_bugs(suggestion: dict) -> list[int]:
    """Ids of the unresolved intermittent-failure bugs a failure line matches."""
    bugs = suggestion.get("bugs") or {}
    matched = []
    for bug in (bugs.get("open_recent") or []) + (bugs.get("all_others") or []):
        keywords = [k.strip() for k in (bug.get("keywords") or "").split(",")]
        if bug.get("id") and not bug.get("resolution"):
            if _INTERMITTENT_KEYWORD in keywords:
                matched.append(bug["id"])
    return matched


def _known_intermittent_bugs(project: str, task_id: str) -> list[int]:
    """Open intermittent bugs Treeherder ties to this task's failure lines.

    Best effort: an empty list on any error.
    """
    try:
        jobs = (
            _get_json(f"{_TREEHERDER}/{project}/jobs/?task_id={task_id}").get("results")
            or []
        )
        if not jobs:
            return []
        suggestions = _get_json(
            f"{_TREEHERDER}/{project}/jobs/{jobs[0]['id']}/bug_suggestions/"
        )
    except (requests.exceptions.RequestException, ValueError, KeyError):
        logger.warning("Could not read bug suggestions for task %s", task_id)
        return []

    bugs: list[int] = []
    for line in suggestions if isinstance(suggestions, list) else []:
        if not (line.get("search") or "").startswith(_FAILURE_LINE_PREFIX):
            continue
        bugs += [bug for bug in _open_intermittent_bugs(line) if bug not in bugs]
    return bugs


def _count_commits(pushes: dict) -> int:
    """Total changesets across the pushlog pushes."""
    return sum(len(p.get("changesets") or []) for p in pushes.values())


def _pushlog(pushlog_url: str, query: str) -> dict:
    """Pushes from one pushlog query; empty on any error."""
    try:
        return _get_json(f"{pushlog_url}?{query}&full=1&version=2").get("pushes") or {}
    except requests.exceptions.RequestException:
        logger.exception("Failed to fetch pushlog %s?%s", pushlog_url, query)
        return {}


def _range_pushes(pushlog_url: str, head_rev: str) -> dict:
    """The head push plus the ``RANGE_PUSHES`` pushes before it."""
    head = _pushlog(pushlog_url, f"changeset={head_rev}")
    if not head:
        return {}
    head_id = max(int(push_id) for push_id in head)
    # startID is exclusive, endID inclusive.
    start_id = max(head_id - RANGE_PUSHES, 0)
    return _pushlog(pushlog_url, f"startID={start_id}&endID={head_id}") or head


def _resolve_range(project: str, head_rev: str, max_commits: int) -> CommitRange | None:
    """The window of commits to search, as a git head plus a depth.

    Deliberately open-ended: pinning the base needs a last-green lookup, and the
    agent gets a better one on demand from ``treeherder-cli --suspects``.
    """
    head_git = _hg_to_git(head_rev)
    if not head_git:
        logger.error("Could not resolve a git hash for head revision %s", head_rev)
        return None

    path = _REPO_PATHS.get(project, project)
    pushes = _range_pushes(f"{_HG_BASE}/{path}/json-pushes", head_rev)
    span = max(_count_commits(pushes), 1)
    return CommitRange(head_git, min(span, max_commits))


def resolve_investigation(
    task_id: str, *, max_commits: int = MAX_RANGE_COMMITS
) -> Investigation:
    """Resolve a failing test task into its investigation context.

    Raises ``ValueError`` when the task has no revision (nothing to investigate).
    """
    task = _get_json(_TC_TASK_URL.format(task_id=task_id))
    tags = task.get("tags") or {}
    project = tags.get("project") or "autoland"
    hg_revision = (task.get("payload") or {}).get("env", {}).get("GECKO_HEAD_REV")
    if not hg_revision:
        raise ValueError(f"task {task_id} has no GECKO_HEAD_REV")
    logger.info("Resolved task %s: project=%s rev=%s", task_id, project, hg_revision)

    label = tags.get("label") or ""
    group_based = not is_no_groups_suite(label)
    groups = _failing_groups(task_id) if group_based else []
    logger.info(
        "Failing groups: %s",
        ", ".join(g.group for g in groups)
        or ("suite reports none" if not group_based else "none resolved"),
    )

    platform = tags.get("test-platform") or ""

    intermittent_bugs = _known_intermittent_bugs(project, task_id)
    logger.info(
        "Known intermittent bugs: %s",
        ", ".join(str(bug) for bug in intermittent_bugs) or "none matched",
    )

    commit_range = _resolve_range(project, hg_revision, max_commits)
    if commit_range is None:
        raise ValueError(f"could not resolve a git commit for task {task_id}")
    logger.info(
        "Searching the %d commit(s) before %s",
        commit_range.span,
        commit_range.head,
    )

    return Investigation(
        project=project,
        hg_revision=hg_revision,
        harness=_harness(tags),
        platform=platform,
        failing_groups=groups,
        commit_range=commit_range,
        label=label,
        group_based=group_based,
        known_intermittent_bugs=intermittent_bugs,
    )
