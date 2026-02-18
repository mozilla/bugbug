# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re
import subprocess
import time
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path

import requests

from bugbug.tools.build_repair.config import (
    TREEHERDER_BASE_URL,
    TRY_PUSH_POLL_INTERVAL_SECONDS,
    TRY_PUSH_TIMEOUT_SECONDS,
)

logger = getLogger(__name__)

_HEADERS = {"User-Agent": "bugbug-build-repair-eval/1.0"}
_LANDO_JOB_ID_RE = re.compile(r"landoCommitID=([A-Za-z0-9_-]+)")


@dataclass
class TryPushResult:
    """Result of local build verification and optional try push submission."""

    local_build_passed: bool
    try_build_passed: bool | None
    lando_job_id: str | None
    treeherder_url: str | None


def _commit_fix(worktree_path: Path, bug_id: int) -> None:
    subprocess.run(
        ["git", "add", "-A"],
        cwd=worktree_path,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", f"Build repair fix for bug {bug_id}"],
        cwd=worktree_path,
        check=True,
    )


def _run_local_build(worktree_path: Path) -> bool:
    result = subprocess.run(
        ["./mach", "build"],
        cwd=worktree_path,
    )
    return result.returncode == 0


def _submit_try(worktree_path: Path, task_name: str) -> tuple[str | None, str | None]:
    result = subprocess.run(
        ["./mach", "try", "fuzzy", "--query", task_name],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    stdout = result.stdout + result.stderr
    match = _LANDO_JOB_ID_RE.search(stdout)
    if not match:
        logger.warning("Could not parse Lando job ID from try output: %s", stdout)
        return None, None

    lando_job_id = match.group(1)
    treeherder_url = f"{TREEHERDER_BASE_URL}/jobs?repo=try&landoCommitID={lando_job_id}"
    return lando_job_id, treeherder_url


def _get_push_revision(lando_job_id: str) -> str | None:
    try:
        resp = requests.get(
            f"{TREEHERDER_BASE_URL}/api/project/try/push/",
            params={"lando_commit_id": lando_job_id},
            headers=_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0].get("revision")
    except Exception:
        logger.exception("Error fetching push revision for lando job %s", lando_job_id)
    return None


def _get_push_by_revision(revision: str) -> dict | None:
    try:
        resp = requests.get(
            f"{TREEHERDER_BASE_URL}/api/project/try/push/",
            params={"revision": revision},
            headers=_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0] if results else None
    except Exception:
        logger.exception("Error fetching push by revision %s", revision)
    return None


def _get_build_job_result(push_id: int, task_name: str) -> str | None:
    try:
        resp = requests.get(
            f"{TREEHERDER_BASE_URL}/api/project/try/jobs/",
            params={"push_id": push_id, "count": 2000},
            headers=_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        for job in resp.json().get("results", []):
            if task_name in job.get("job_type_name", ""):
                if job["state"] != "completed":
                    return job["state"]
                return job["result"]
    except Exception:
        logger.exception("Error fetching build job result for push %d", push_id)
    return None


def _poll_treeherder(lando_job_id: str, task_name: str) -> bool | None:
    deadline = time.monotonic() + TRY_PUSH_TIMEOUT_SECONDS
    push_id: int | None = None

    while time.monotonic() < deadline:
        if push_id is None:
            revision = _get_push_revision(lando_job_id)
            if revision:
                push = _get_push_by_revision(revision)
                if push:
                    push_id = push["id"]

        if push_id is not None:
            result = _get_build_job_result(push_id, task_name)
            if result == "success":
                return True
            if result in ("busted", "testfailed", "exception"):
                return False

        time.sleep(TRY_PUSH_POLL_INTERVAL_SECONDS)

    logger.warning("Try push polling timed out for lando job %s", lando_job_id)
    return None


def run_try_verification(
    worktree_path: Path,
    bug_id: int,
    task_name: str,
    skip_try_push: bool = False,
) -> TryPushResult:
    _commit_fix(worktree_path, bug_id)

    local_passed = _run_local_build(worktree_path)
    if not local_passed:
        return TryPushResult(
            local_build_passed=False,
            try_build_passed=None,
            lando_job_id=None,
            treeherder_url=None,
        )

    if skip_try_push:
        return TryPushResult(
            local_build_passed=True,
            try_build_passed=None,
            lando_job_id=None,
            treeherder_url=None,
        )

    lando_job_id, treeherder_url = _submit_try(worktree_path, task_name)
    if not lando_job_id:
        return TryPushResult(
            local_build_passed=True,
            try_build_passed=None,
            lando_job_id=None,
            treeherder_url=None,
        )

    try_passed = _poll_treeherder(lando_job_id, task_name)
    return TryPushResult(
        local_build_passed=True,
        try_build_passed=try_passed,
        lando_job_id=lando_job_id,
        treeherder_url=treeherder_url,
    )
