# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Resolve a failing Taskcluster test task into everything the agent needs.

From a task id alone, derive the push it belongs to (project + hg revision), the
test groups that failed, the revision at which the failing group was last green,
and the git commits that landed since then (head first). That commit range both
bounds the culprit search and sizes the shallow clone. The agent recomputes all
of this itself so its only input is a task id; the pulse listener uses the same
public Taskcluster / mozci / hg-pushlog / lando lookups only to decide which
failures are worth investigating.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import mozci.push  # noqa: F401  (imported so mozci registers its data sources)
import requests
from mozci import data
from mozci.errors import ParentPushNotFound
from mozci.push import MAX_DEPTH, Push
from mozci.task import Status

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
# Hard cap so an old last-green can't produce an unbounded clone depth.
MAX_CANDIDATES = 50


@dataclass(frozen=True)
class FailingGroup:
    """A failing test manifest and a representative failing test within it."""

    group: str
    test: str


@dataclass
class Investigation:
    """The resolved context for one test-repair run, derived from a task id."""

    project: str
    hg_revision: str
    harness: str
    failing_groups: list[FailingGroup]
    last_green_revision: str | None
    # Git hashes of the commits to inspect, head (failure) commit first.
    candidate_commits: list[str]

    @property
    def failure_commit(self) -> str:
        return self.candidate_commits[0]


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
    return tags.get("kind") or suite or "unknown"


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
        test, _ftype = fails[0]
        groups.append(FailingGroup(group=group, test=test))
    return groups


def _group_status(push: Push, group: str) -> str | None:
    """'passed'/'failed'/None for a test group on a push (None = non-decisive)."""
    summary = push.group_summaries.get(group)
    if summary is None:
        return None
    if summary.status == Status.PASS:
        return "passed"
    if summary.status == Status.FAIL:
        return "failed"
    # INTERMITTENT or still running: can't anchor last-green here.
    return None


def _last_green(branch: str, rev: str, group: str) -> str | None:
    """Most recent ancestor revision where ``group`` was green, best effort.

    Returns None when no green ancestor is found within MAX_DEPTH, the group was
    already failing upstream, or mozci errors -- the caller then falls back to the
    failing push alone.
    """
    try:
        ancestor = Push(rev, branch=branch)
        for _ in range(MAX_DEPTH):
            try:
                ancestor = ancestor.parent
            except ParentPushNotFound:
                break
            status = _group_status(ancestor, group)
            if status == "passed":
                return ancestor.rev
            if status == "failed":
                return None
    except Exception:
        logger.exception("Could not determine last-green for %s at %s", group, rev)
    return None


def _commits_from_pushes(pushes: dict) -> list[tuple[str | None, str | None]]:
    """Flatten pushlog pushes into (hg_node, git_hash) pairs, oldest first."""
    pairs: list[tuple[str | None, str | None]] = []
    for key in sorted(pushes, key=int):
        push = pushes[key]
        changesets = push.get("changesets") or []
        git_changesets = push.get("git_changesets") or []
        for i, cs in enumerate(changesets):
            node = cs.get("node") if isinstance(cs, dict) else cs
            git = git_changesets[i] if i < len(git_changesets) else None
            pairs.append((node, git))
    return pairs


def _candidate_commits(
    project: str,
    head_rev: str,
    last_green_rev: str | None,
    max_candidates: int,
) -> list[str]:
    """Git hashes for commits in ``(last_green_rev, head_rev]``, head first.

    Falls back to the head push when there is no last-green, and to the single
    head commit when the pushlog can't be read, so the result always includes the
    head. Capped to ``max_candidates`` newest commits.
    """
    path = _REPO_PATHS.get(project, project)
    base = f"{_HG_BASE}/{path}/json-pushes"
    try:
        if last_green_rev:
            url = (
                f"{base}?fromchange={last_green_rev}"
                f"&tochange={head_rev}&full=1&version=2"
            )
        else:
            url = f"{base}?changeset={head_rev}&full=1&version=2"
        pushes = _get_json(url).get("pushes") or {}
    except requests.exceptions.RequestException:
        logger.exception(
            "Failed to fetch %s pushlog (%s..%s)", project, last_green_rev, head_rev
        )
        pushes = {}

    git_commits: list[str] = []
    for node, git in _commits_from_pushes(pushes):
        if not git and node:
            git = _hg_to_git(node)
        if git:
            git_commits.append(git)
    git_commits.reverse()  # pushlog is oldest-first; we want head first.

    if not git_commits:
        head_git = _hg_to_git(head_rev)
        return [head_git] if head_git else []

    if len(git_commits) > max_candidates:
        logger.warning(
            "Candidate range %s..%s has %d commits; capping to the newest %d",
            last_green_rev,
            head_rev,
            len(git_commits),
            max_candidates,
        )
        git_commits = git_commits[:max_candidates]
    return git_commits


def resolve_investigation(
    task_id: str, *, max_candidates: int = MAX_CANDIDATES
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

    last_green = _last_green(project, hg_revision, groups[0].group) if groups else None
    logger.info("Last-green revision: %s", last_green or "not found")

    candidate_commits = _candidate_commits(
        project, hg_revision, last_green, max_candidates
    )
    if not candidate_commits:
        raise ValueError(f"could not resolve any git commit for task {task_id}")
    logger.info(
        "Resolved %d candidate commit(s), head %s",
        len(candidate_commits),
        candidate_commits[0],
    )

    return Investigation(
        project=project,
        hg_revision=hg_revision,
        harness=_harness(tags),
        failing_groups=groups,
        last_green_revision=last_green,
        candidate_commits=candidate_commits,
    )
