# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Resolve a failing Taskcluster test task into everything the agent needs.

From a task id alone, derive the push it belongs to (project + hg revision), the
tests that failed, the revision at which those tests were last green on the same
platform, and the git range that landed since then. Only the range endpoints are
mapped to git -- the agent enumerates the commits between them with ``git log``
in the checkout. The agent recomputes all of this itself so its only input is a
task id; the pulse listener uses the same public Taskcluster / mozci / hg-pushlog
/ lando lookups only to decide which failures are worth investigating.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import mozci.push  # noqa: F401  (imported so mozci registers its data sources)
import requests
from mozci import data
from mozci.errors import ParentPushNotFound
from mozci.push import MAX_DEPTH, Push
from mozci.task import Status, is_no_groups_suite

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
# Ancestor pushes to walk looking for a green run. Each step is a live, uncached
# group_summaries lookup, so raising this trades startup latency for a better range.
LAST_GREEN_MAX_DEPTH = MAX_DEPTH
# Cap on commits (not pushes) in the range, bounding the clone depth.
MAX_RANGE_COMMITS = 100
FALLBACK_RANGE_PUSHES = 20


@dataclass(frozen=True)
class FailingGroup:
    """A failing test manifest and every test that failed in it."""

    group: str
    tests: list[str]


@dataclass(frozen=True)
class CommitRange:
    """The git commit range to search for the culprit."""

    # The failure commit; what the checkout is pinned to.
    head: str
    # The last-green commit, exclusive. None when unknown or outside the cap.
    base: str | None
    # Commits in the range; bounds the shallow clone depth.
    span: int
    # Whether the culprit is provably inside the range.
    complete: bool


@dataclass
class Investigation:
    """The resolved context for one test-repair run, derived from a task id."""

    project: str
    hg_revision: str
    harness: str
    # Taskcluster ``test-platform`` tag, e.g. "linux1804-64-qr/debug".
    platform: str
    failing_groups: list[FailingGroup]
    last_green_revision: str | None
    commit_range: CommitRange
    # Full task label, e.g. "test-linux1804-64-qr/debug-mochitest-browser-chrome-1".
    # Unlike ``platform`` it also carries the test variant and chunk, which is what
    # distinguishes two runs of the same suite on the same OS and build type.
    label: str = ""
    # False for suites that report no test manifests (gtest, jittest, talos, ...),
    # where ``failing_groups`` is empty by nature rather than because the lookup
    # failed, and blame has to be anchored on the task as a whole.
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
        """The sanitizer CI built with, if any; a plain build cannot trigger it."""
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


def _same_platform(task, platform: str) -> bool:
    return task.platform == platform or platform in (task.label or "")


def _test_status(push: Push, group: str, tests: list[str], platform: str) -> str | None:
    """'passed'/'failed'/None for ``tests`` of ``group`` on ``platform``.

    Restricted to the failing tests and the failing platform: ``GroupSummary``
    aggregates every platform and every test in the manifest, so a push where the
    group ran green on Windows -- or where only other tests in it passed -- must
    not anchor a last-green for a Linux-only failure. None means non-decisive
    (the group did not run here, or is intermittent/unfinished).
    """
    summary = push.group_summaries.get(group)
    if summary is None:
        return None
    wanted = set(tests)
    statuses = set()
    for task in summary.tasks:
        if not _same_platform(task, platform):
            continue
        for result in task.results:
            if result.group != group:
                continue
            if result.ok:
                statuses.add("passed")
                continue
            failed = {test for test, _type in task.failure_types.get(group, [])}
            statuses.add("failed" if not wanted or wanted & failed else "passed")
    if "failed" in statuses:
        return "failed"
    return "passed" if statuses else None


def _label_status(push: Push, label: str) -> str | None:
    """'passed'/'failed'/None for a whole task label on ``push``.

    The fallback for suites that report no test manifests, where there is no group
    to key on and the task is the finest granularity available. The label already
    pins the platform, build type and variant, and ``label_summaries`` excludes
    taskgraph-chunked tasks -- whose label covers different tests on each push --
    so only genuinely comparable runs are considered.
    """
    summary = push.label_summaries.get(label)
    if summary is None:
        return None
    if summary.status == Status.PASS:
        return "passed"
    if summary.status == Status.FAIL:
        return "failed"
    # INTERMITTENT: it both passed and failed here, so it cannot anchor a green.
    return None


def _walk_ancestors(
    branch: str, rev: str, status_of, max_depth: int = LAST_GREEN_MAX_DEPTH
) -> str | None:
    """Most recent ancestor revision that ``status_of`` reports as 'passed'.

    Best effort: None when no green ancestor is found within ``max_depth``, the
    failure was already there upstream, or mozci errors.
    """
    try:
        ancestor = Push(rev, branch=branch)
        for _ in range(max_depth):
            try:
                ancestor = ancestor.parent
            except ParentPushNotFound:
                break
            status = status_of(ancestor)
            if status == "passed":
                return ancestor.rev
            if status == "failed":
                return None
    except Exception:
        logger.exception("Could not determine last-green at %s", rev)
    return None


def _last_green(
    branch: str,
    rev: str,
    failing: FailingGroup,
    platform: str,
    max_depth: int = LAST_GREEN_MAX_DEPTH,
) -> str | None:
    """Most recent ancestor revision where the failing tests were green."""
    return _walk_ancestors(
        branch,
        rev,
        lambda push: _test_status(push, failing.group, failing.tests, platform),
        max_depth,
    )


def _last_green_label(
    branch: str, rev: str, label: str, max_depth: int = LAST_GREEN_MAX_DEPTH
) -> str | None:
    """Most recent ancestor revision where the whole failing task was green."""
    return _walk_ancestors(
        branch, rev, lambda push: _label_status(push, label), max_depth
    )


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


def _fallback_pushes(pushlog_url: str, head_rev: str) -> dict:
    """The head push plus the ``FALLBACK_RANGE_PUSHES`` pushes before it."""
    head = _pushlog(pushlog_url, f"changeset={head_rev}")
    if not head:
        return {}
    head_id = max(int(push_id) for push_id in head)
    # startID is exclusive, endID inclusive.
    start_id = max(head_id - FALLBACK_RANGE_PUSHES, 0)
    return _pushlog(pushlog_url, f"startID={start_id}&endID={head_id}") or head


def _resolve_range(
    project: str,
    head_rev: str,
    last_green_rev: str | None,
    max_commits: int,
) -> CommitRange | None:
    """Resolve ``(last_green_rev, head_rev]`` into git endpoints and a commit count.

    None when the head cannot be mapped: pinning the checkout to an older commit
    would blame the wrong change. ``base`` is dropped when unknown or outside the
    capped clone depth, which also marks the range incomplete.
    """
    head_git = _hg_to_git(head_rev)
    if not head_git:
        logger.error("Could not resolve a git hash for head revision %s", head_rev)
        return None

    path = _REPO_PATHS.get(project, project)
    pushlog_url = f"{_HG_BASE}/{path}/json-pushes"
    if last_green_rev:
        pushes = _pushlog(
            pushlog_url, f"fromchange={last_green_rev}&tochange={head_rev}"
        )
    else:
        pushes = _fallback_pushes(pushlog_url, head_rev)

    span = max(_count_commits(pushes), 1)
    if not last_green_rev or not pushes:
        return CommitRange(head_git, None, min(span, max_commits), False)

    if span > max_commits:
        logger.warning(
            "Range %s..%s has %d commits; capping the clone to the newest %d",
            last_green_rev,
            head_rev,
            span,
            max_commits,
        )
        return CommitRange(head_git, None, max_commits, False)

    base_git = _hg_to_git(last_green_rev)
    if not base_git:
        logger.warning("Could not resolve a git hash for last-green %s", last_green_rev)
        return CommitRange(head_git, None, span, False)
    return CommitRange(head_git, base_git, span, True)


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
    # Group-less suites have nothing finer than the task to compare across pushes.
    # A grouped suite whose lookup failed gets no last-green rather than a
    # label-level one: its tasks are chunked, so the same label covers different
    # tests on each push and ``label_summaries`` deliberately omits them.
    if groups:
        last_green = _last_green(project, hg_revision, groups[0], platform)
    elif not group_based and label:
        last_green = _last_green_label(project, hg_revision, label)
    else:
        last_green = None
    logger.info("Last-green revision: %s", last_green or "not found")

    intermittent_bugs = _known_intermittent_bugs(project, task_id)
    logger.info(
        "Known intermittent bugs: %s",
        ", ".join(str(bug) for bug in intermittent_bugs) or "none matched",
    )

    commit_range = _resolve_range(project, hg_revision, last_green, max_commits)
    if commit_range is None:
        raise ValueError(f"could not resolve a git commit for task {task_id}")
    logger.info(
        "Range %s..%s spans %d commit(s), complete: %s",
        commit_range.base or "(unknown)",
        commit_range.head,
        commit_range.span,
        commit_range.complete,
    )

    return Investigation(
        project=project,
        hg_revision=hg_revision,
        harness=_harness(tags),
        platform=platform,
        failing_groups=groups,
        last_green_revision=last_green,
        commit_range=commit_range,
        label=label,
        group_based=group_based,
        known_intermittent_bugs=intermittent_bugs,
    )
