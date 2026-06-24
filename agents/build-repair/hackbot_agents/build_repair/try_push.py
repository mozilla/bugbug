# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
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


def _mach_env(worktree_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["MOZBUILD_STATE_PATH"] = str(worktree_path / ".mozbuild")
    return env


@dataclass
class TryPushResult:
    """Result of local build verification and optional try push submission."""

    local_build_passed: bool
    try_build_passed: bool | None
    lando_job_id: str | None
    treeherder_url: str | None


def _commit_fix(worktree_path: Path, bug_id: int) -> None:
    logger.info("Committing fix for bug %s in %s", bug_id, worktree_path)
    subprocess.run(
        ["git", "add", "-A"],
        cwd=worktree_path,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=bugbug",
            "-c",
            "user.email=bugbug@mozilla.com",
            "commit",
            "-m",
            f"Build repair fix for bug {bug_id}",
        ],
        cwd=worktree_path,
        check=True,
    )
    logger.info("Bug %s: fix committed", bug_id)


def _run_subprocess(
    cmd: list[str], worktree_path: Path, capture: bool
) -> subprocess.CompletedProcess[str]:
    if capture:
        return subprocess.run(
            cmd,
            cwd=worktree_path,
            env=_mach_env(worktree_path),
            capture_output=True,
            text=True,
        )
    return subprocess.run(
        cmd,
        cwd=worktree_path,
        env=_mach_env(worktree_path),
        text=True,
    )


def _run_local_build(worktree_path: Path) -> bool:
    capture = not logger.isEnabledFor(logging.DEBUG)

    logger.info("Running bootstrap in %s", worktree_path)
    result = _run_subprocess(
        ["./mach", "--no-interactive", "bootstrap"], worktree_path, capture
    )
    if result.returncode != 0:
        if capture and result.stderr:
            logger.warning("Bootstrap stderr:\n%s", result.stderr[-2000:])
        raise RuntimeError(
            f"Local bootstrap failed with return code {result.returncode}"
        )

    logger.info("Running local build in %s", worktree_path)
    result = _run_subprocess(["./mach", "build"], worktree_path, capture)
    passed = result.returncode == 0
    status = "passed" if passed else "failed"
    logger.info("Local build %s (returncode=%s)", status, result.returncode)
    if not passed and capture and result.stderr:
        logger.warning("Build stderr:\n%s", result.stderr[-2000:])
    return passed


def _submit_try(worktree_path: Path, task_name: str) -> tuple[str | None, str | None]:
    logger.info("Submitting try push for task=%s in %s", task_name, worktree_path)
    result = subprocess.run(
        ["./mach", "try", "fuzzy", "--query", task_name],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        env=_mach_env(worktree_path),
    )
    stdout = result.stdout + result.stderr
    logger.debug("Try push output: %s", stdout)
    match = _LANDO_JOB_ID_RE.search(stdout)
    if not match:
        logger.warning("Could not parse Lando job ID from try output: %s", stdout)
        return None, None

    lando_job_id = match.group(1)
    treeherder_url = f"{TREEHERDER_BASE_URL}/jobs?repo=try&landoCommitID={lando_job_id}"
    logger.info(
        "Try push submitted: lando_job_id=%s, treeherder=%s",
        lando_job_id,
        treeherder_url,
    )
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
        logger.exception("Error fetching build job result for push %s", push_id)
    return None


def _poll_treeherder(lando_job_id: str, task_name: str) -> bool | None:
    logger.info(
        "Polling Treeherder for lando_job_id=%s, task=%s (timeout=%ss, interval=%ss)",
        lando_job_id,
        task_name,
        TRY_PUSH_TIMEOUT_SECONDS,
        TRY_PUSH_POLL_INTERVAL_SECONDS,
    )
    deadline = time.monotonic() + TRY_PUSH_TIMEOUT_SECONDS
    push_id: int | None = None
    poll_count = 0

    while time.monotonic() < deadline:
        poll_count += 1
        if push_id is None:
            revision = _get_push_revision(lando_job_id)
            if revision:
                logger.info(
                    "Resolved revision=%s for lando_job_id=%s", revision, lando_job_id
                )
                push = _get_push_by_revision(revision)
                if push:
                    push_id = push["id"]
                    logger.info(
                        "Resolved push_id=%s for revision=%s", push_id, revision
                    )

        if push_id is not None:
            result = _get_build_job_result(push_id, task_name)
            logger.debug(
                "Poll #%s: job result=%s for push_id=%s", poll_count, result, push_id
            )
            if result == "success":
                logger.info("Try build succeeded for lando_job_id=%s", lando_job_id)
                return True
            if result in ("busted", "testfailed", "exception"):
                logger.info(
                    "Try build failed (%s) for lando_job_id=%s", result, lando_job_id
                )
                return False
        else:
            logger.debug(
                "Poll #%s: push not yet available for lando_job_id=%s",
                poll_count,
                lando_job_id,
            )
        time.sleep(TRY_PUSH_POLL_INTERVAL_SECONDS)

    logger.warning(
        "Try push polling timed out after %s polls for lando job %s",
        poll_count,
        lando_job_id,
    )
    return None


def run_try_verification(
    worktree_path: Path,
    bug_id: int,
    task_name: str,
    skip_try_push: bool = False,
) -> TryPushResult:
    logger.info(
        "Starting try verification for bug %s (task=%s, skip_try_push=%s)",
        bug_id,
        task_name,
        skip_try_push,
    )
    _commit_fix(worktree_path, bug_id)

    local_passed = _run_local_build(worktree_path)
    if not local_passed:
        logger.warning("Bug %s: local build failed, skipping try push", bug_id)
        return TryPushResult(
            local_build_passed=False,
            try_build_passed=None,
            lando_job_id=None,
            treeherder_url=None,
        )

    if skip_try_push:
        logger.info(
            "Bug %s: local build passed, skipping try push as requested", bug_id
        )
        return TryPushResult(
            local_build_passed=True,
            try_build_passed=None,
            lando_job_id=None,
            treeherder_url=None,
        )

    lando_job_id, treeherder_url = _submit_try(worktree_path, task_name)
    if not lando_job_id:
        logger.warning("Bug %s: try push submission failed, no lando job ID", bug_id)
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
