# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Resolve a Taskcluster build-failure task into the push to repair.

Given a failing build task id, look up its push: the failure (head) commit the
tree is checked out at, plus the other commits that landed in the same push so
the agent can blame the one that broke the build. Uses the same public
Taskcluster / lando / pushlog lookups the pulse listener does, so the agent
derives everything it reports from a task id.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

_TC_TASK_URL = "https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/{task_id}"
_LANDO_HG2GIT = "https://lando.moz.tools/api/hg2git/firefox/{rev}"
_HG_BASE = "https://hg.mozilla.org"
# Taskcluster ``project`` tag -> hg pushlog repository path. Unknown projects
# fall back to a same-named repo under the hg base url.
_REPO_PATHS = {
    "autoland": "integration/autoland",
    "mozilla-central": "mozilla-central",
    "mozilla-beta": "releases/mozilla-beta",
    "mozilla-release": "releases/mozilla-release",
    "try": "try",
}
_HEADERS = {"User-Agent": "hackbot-build-repair/1.0"}
_TIMEOUT = 30

# Every Firefox commit message opens with the bug it landed ("Bug 123 - ..."),
# which is where a run gets its bug: it is never an input.
_BUG_RE = re.compile(r"^Bug (\d+)", re.IGNORECASE)


def _get_json(url: str) -> dict:
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _task(task_id: str) -> dict:
    return _get_json(_TC_TASK_URL.format(task_id=task_id))


def _task_push(task: dict) -> tuple[str | None, str | None]:
    tags = task.get("tags") or {}
    return (
        tags.get("project"),
        (task.get("payload") or {}).get("env", {}).get("GECKO_HEAD_REV"),
    )


def _hg_to_git(rev: str) -> str:
    return _get_json(_LANDO_HG2GIT.format(rev=rev))["git_hash"]


def _bug_from_desc(desc: str) -> int | None:
    """The bug a changeset landed for, from the first line of its description."""
    match = _BUG_RE.match(desc.strip())
    return int(match.group(1)) if match else None


def _push_git_commits(project: str, rev: str) -> tuple[list[str], dict[str, int]]:
    """The push that landed ``rev`` (pushlog order, oldest first) and its bugs.

    The pushlog exposes a ``git_changesets`` array parallel to ``changesets``;
    when a git hash is missing we map that changeset via lando. ``full=1`` also
    returns each changeset's ``desc``, which is what names the bug, so no extra
    request is needed to pair the two.
    """
    path = _REPO_PATHS.get(project, project)
    url = f"{_HG_BASE}/{path}/json-pushes?changeset={rev}&full=1&version=2"
    pushes = _get_json(url).get("pushes") or {}
    push = next(iter(pushes.values()), None)
    if not push:
        return [], {}
    git_changesets = push.get("git_changesets") or []
    changesets = push.get("changesets") or []
    commits = []
    bugs: dict[str, int] = {}
    for i, cs in enumerate(changesets):
        git_commit = git_changesets[i] if i < len(git_changesets) else None
        if not git_commit:
            node = cs.get("node") if isinstance(cs, dict) else cs
            git_commit = _hg_to_git(node) if node else None
        if git_commit:
            commits.append(git_commit)
            bug_id = _bug_from_desc(cs.get("desc", "") if isinstance(cs, dict) else "")
            if bug_id is not None:
                bugs[git_commit] = bug_id
    return commits, bugs


@dataclass(frozen=True)
class PushInfo:
    """The push a failing task belongs to.

    ``project`` and ``hg_revision`` are what Treeherder is keyed on, so they are
    kept alongside the git commits rather than discarded after the lookup.
    """

    project: str | None
    hg_revision: str | None
    git_commits: list[str]
    # ``createdForUser``: who pushed the change that failed to build.
    developer_email: str | None = None
    # Each push commit mapped to the bug it landed for, from its pushlog
    # description. A run is never told which bug a failure belongs to.
    commit_bugs: dict[str, int] = field(default_factory=dict)


def task_push(task_id: str) -> tuple[str | None, str | None]:
    """The ``(project, hg_revision)`` a task ran on; what Treeherder is keyed on."""
    return _task_push(_task(task_id))


def resolve_push(task_id: str, git_commit: str | None = None) -> PushInfo:
    """Resolve a failing task into its push, failure commit first.

    ``git_commit`` overrides the failure commit (skipping the lando lookup); the
    task is still fetched for its revision. Raises on network errors or when the
    failure commit cannot be determined.
    """
    task = _task(task_id)
    project, hg_rev = _task_push(task)

    push, commit_bugs = (
        _push_git_commits(project, hg_rev) if hg_rev and project else ([], {})
    )

    failure_commit = git_commit
    if not failure_commit:
        if not hg_rev:
            raise ValueError(
                f"task {task_id} has no GECKO_HEAD_REV and no git_commit override"
            )
        failure_commit = _hg_to_git(hg_rev)

    return PushInfo(
        project=project,
        hg_revision=hg_rev,
        git_commits=[failure_commit] + [c for c in push if c != failure_commit],
        developer_email=(task.get("tags") or {}).get("createdForUser"),
        commit_bugs=commit_bugs,
    )
