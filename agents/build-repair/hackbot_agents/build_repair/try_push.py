"""Optional Firefox try-server push tool.

Submits the current source checkout to the Firefox try server via ``./mach try``
and, optionally, polls Treeherder for the build result. Exposed as a separate
``TRY_TOOLS`` list (not the default firefox ``TOOLS``) so an agent only gains the
capability when it explicitly wires it in — a try push is an outward-facing
action that not every run should perform.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Annotated, Any

import requests
from agent_tools.registry import ToolError, tool, tools_in
from pydantic import Field

logger = logging.getLogger(__name__)

TREEHERDER_BASE_URL = "https://treeherder.mozilla.org"
_HEADERS = {"User-Agent": "hackbot-build-repair/1.0"}
_LANDO_JOB_ID_RE = re.compile(r"landoCommitID=([A-Za-z0-9_-]+)")


def _commit_all(source_dir: Path) -> None:
    """Commit the working tree so ``./mach try`` has a commit to push."""
    subprocess.run(["git", "add", "-A"], cwd=source_dir, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=hackbot",
            "-c",
            "user.email=hackbot@mozilla.com",
            "commit",
            "--allow-empty",
            "-m",
            "Build repair candidate fix",
        ],
        cwd=source_dir,
        check=True,
    )


def _submit_try(source_dir: Path, task_name: str) -> tuple[str | None, str | None]:
    result = subprocess.run(
        ["./mach", "try", "fuzzy", "--query", task_name],
        cwd=source_dir,
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


def _get_push_id(revision: str) -> int | None:
    try:
        resp = requests.get(
            f"{TREEHERDER_BASE_URL}/api/project/try/push/",
            params={"revision": revision},
            headers=_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0]["id"] if results else None
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


def _poll_treeherder(
    lando_job_id: str, task_name: str, timeout_seconds: int, interval_seconds: int
) -> bool | None:
    deadline = time.monotonic() + timeout_seconds
    push_id: int | None = None
    while time.monotonic() < deadline:
        if push_id is None:
            revision = _get_push_revision(lando_job_id)
            if revision:
                push_id = _get_push_id(revision)
        if push_id is not None:
            result = _get_build_job_result(push_id, task_name)
            if result == "success":
                return True
            if result in ("busted", "testfailed", "exception"):
                return False
        time.sleep(interval_seconds)
    logger.warning("Try push polling timed out for lando job %s", lando_job_id)
    return None


def run_try_push(
    source_dir: Path,
    task_name: str,
    poll: bool,
    timeout_seconds: int,
    interval_seconds: int,
) -> dict[str, Any]:
    """Commit the working tree, submit a try push, and optionally poll for the result."""
    _commit_all(source_dir)
    lando_job_id, treeherder_url = _submit_try(source_dir, task_name)
    if not lando_job_id:
        raise ToolError(
            "Try push submission failed: no Lando job id in ./mach try output",
            payload={"error": "try_submit_failed"},
        )
    result: dict[str, Any] = {
        "submitted": True,
        "lando_job_id": lando_job_id,
        "treeherder_url": treeherder_url,
        "try_build_passed": None,
    }
    if poll:
        result["try_build_passed"] = _poll_treeherder(
            lando_job_id, task_name, timeout_seconds, interval_seconds
        )
    return result


@tool
async def submit_try_push(
    ctx,
    task_name: Annotated[
        str,
        Field(
            description=(
                "Treeherder task name to build/select on try, e.g. "
                "'build-linux64/opt'. The failing task is the natural choice."
            )
        ),
    ],
    poll: Annotated[
        bool,
        Field(
            description=(
                "Poll Treeherder until the build job completes (up to timeout) "
                "and report pass/fail. If false, submit and return immediately."
            )
        ),
    ] = True,
    timeout_seconds: Annotated[
        int, Field(description="Max seconds to poll Treeherder (default 7200).")
    ] = 7200,
    poll_interval_seconds: Annotated[
        int, Field(description="Seconds between Treeherder polls (default 60).")
    ] = 60,
) -> dict:
    """Submit the current Firefox checkout to the try server and check the build.

    Commits the working tree as a candidate fix, runs ``./mach try fuzzy --query
    <task_name>`` to push it, and (when ``poll`` is true) watches Treeherder for
    the named build job. Returns JSON: submitted (bool), lando_job_id (str),
    treeherder_url (str), try_build_passed (bool|null — null when polling was
    skipped or timed out). Slow: a try build can take well over an hour, so only
    call this once you are confident the fix builds locally.
    """
    return await asyncio.to_thread(
        run_try_push,
        ctx.source_dir,
        task_name,
        poll,
        timeout_seconds,
        poll_interval_seconds,
    )


TRY_TOOLS = tools_in(__name__)
