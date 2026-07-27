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
from dataclasses import dataclass

import mozci.push  # noqa: F401  (imported so mozci registers its data sources)
import requests
from mozci import data
from mozci.errors import ParentPushNotFound
from mozci.push import MAX_DEPTH, Push

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
# Ancestor pushes to walk looking for a green run. Each step is a live, uncached
# group_summaries lookup, so raising this trades startup latency for a better range.
LAST_GREEN_MAX_DEPTH = MAX_DEPTH
# Cap on commits (not pushes) in the range, bounding the clone depth.
MAX_RANGE_COMMITS = 100


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


def _last_green(
    branch: str,
    rev: str,
    failing: FailingGroup,
    platform: str,
    max_depth: int = LAST_GREEN_MAX_DEPTH,
) -> str | None:
    """Most recent ancestor revision where the failing tests were green.

    Best effort: None when no green ancestor is found within ``max_depth``, the
    tests were already failing upstream, or mozci errors.
    """
    try:
        ancestor = Push(rev, branch=branch)
        for _ in range(max_depth):
            try:
                ancestor = ancestor.parent
            except ParentPushNotFound:
                break
            status = _test_status(ancestor, failing.group, failing.tests, platform)
            if status == "passed":
                return ancestor.rev
            if status == "failed":
                return None
    except Exception:
        logger.exception(
            "Could not determine last-green for %s at %s", failing.group, rev
        )
    return None


def _count_commits(pushes: dict) -> int:
    """Total changesets across the pushlog pushes."""
    return sum(len(p.get("changesets") or []) for p in pushes.values())


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
    pushlog = f"{_HG_BASE}/{path}/json-pushes"
    try:
        if last_green_rev:
            url = (
                f"{pushlog}?fromchange={last_green_rev}"
                f"&tochange={head_rev}&full=1&version=2"
            )
        else:
            url = f"{pushlog}?changeset={head_rev}&full=1&version=2"
        pushes = _get_json(url).get("pushes") or {}
    except requests.exceptions.RequestException:
        logger.exception(
            "Failed to fetch %s pushlog (%s..%s)", project, last_green_rev, head_rev
        )
        pushes = {}

    span = max(_count_commits(pushes), 1)
    if not last_green_rev or not pushes:
        return CommitRange(head_git, None, span, False)

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

    groups = _failing_groups(task_id)
    logger.info(
        "Failing groups: %s", ", ".join(g.group for g in groups) or "none resolved"
    )

    platform = tags.get("test-platform") or ""
    last_green = (
        _last_green(project, hg_revision, groups[0], platform) if groups else None
    )
    logger.info("Last-green revision: %s", last_green or "not found")

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
    )
