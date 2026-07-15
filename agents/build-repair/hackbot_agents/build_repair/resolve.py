# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Resolve a Taskcluster build-failure task into the commits to repair.

Given a failing build task id, look up its push: the failure (head) commit the
tree is checked out at, plus the other commits that landed in the same push so
the agent can blame the one that broke the build. Uses the same public
Taskcluster / lando / pushlog lookups the pulse listener does, so the agent
derives the commits itself from a task id.
"""

from __future__ import annotations

import logging

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


def _get_json(url: str) -> dict:
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _hg_to_git(rev: str) -> str:
    return _get_json(_LANDO_HG2GIT.format(rev=rev))["git_hash"]


def _push_git_commits(project: str, rev: str) -> list[str]:
    """Git hashes of the push that landed ``rev`` (pushlog order, oldest first).

    The pushlog exposes a ``git_changesets`` array parallel to ``changesets``;
    when a git hash is missing we map that changeset via lando.
    """
    path = _REPO_PATHS.get(project, project)
    url = f"{_HG_BASE}/{path}/json-pushes?changeset={rev}&full=1&version=2"
    pushes = _get_json(url).get("pushes") or {}
    push = next(iter(pushes.values()), None)
    if not push:
        return []
    git_changesets = push.get("git_changesets") or []
    changesets = push.get("changesets") or []
    commits = []
    for i, cs in enumerate(changesets):
        git_commit = git_changesets[i] if i < len(git_changesets) else None
        if not git_commit:
            node = cs.get("node") if isinstance(cs, dict) else cs
            git_commit = _hg_to_git(node) if node else None
        if git_commit:
            commits.append(git_commit)
    return commits


def resolve_git_commits(task_id: str, git_commit: str | None = None) -> list[str]:
    """Resolve a failing task into its push commits, failure commit first.

    ``git_commit`` overrides the failure commit (skipping the lando lookup); the
    task is still fetched for its revision. Raises on network errors or when the
    failure commit cannot be determined.
    """
    task = _get_json(_TC_TASK_URL.format(task_id=task_id))
    tags = task.get("tags") or {}
    project = tags.get("project")
    hg_rev = (task.get("payload") or {}).get("env", {}).get("GECKO_HEAD_REV")

    push = _push_git_commits(project, hg_rev) if hg_rev and project else []

    failure_commit = git_commit
    if not failure_commit:
        if not hg_rev:
            raise ValueError(
                f"task {task_id} has no GECKO_HEAD_REV and no git_commit override"
            )
        failure_commit = _hg_to_git(hg_rev)

    return [failure_commit] + [c for c in push if c != failure_commit]
