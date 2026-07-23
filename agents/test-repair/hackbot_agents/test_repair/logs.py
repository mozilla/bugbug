# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Download and sanitize a failing test task's log.

Before invoking Claude we fetch each failing task's latest-run
``live_backing.log`` and write two files to the scratch dir: the full log and a
sanitized companion that keeps only the interesting lines -- the test harness's
``TEST-UNEXPECTED-*`` result lines plus ``ERROR -`` / ``FATAL -`` lines. The
agent starts from the sanitized log so its context isn't drowned by tens of MB
of output, and falls back to the full log for surrounding detail.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import NamedTuple

import requests

logger = logging.getLogger(__name__)

ARTIFACT_URL = (
    "https://firefox-ci-tc.services.mozilla.com/api/queue/v1/"
    "task/{task_id}/artifacts/public/logs/live_backing.log"
)
_HEADERS = {"User-Agent": "hackbot-test-repair/1.0"}
_TIMEOUT = 120
_MAX_LINES = 2000

_INTERESTING_RE = re.compile(r"TEST-UNEXPECTED-|(?:ERROR|FATAL) -")


class TaskLogs(NamedTuple):
    """Paths to the two log files written for one failing task."""

    sanitized: Path
    full: Path


def _safe_filename(task_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", task_name).strip("_") or "task"


def sanitize_log(text: str) -> str:
    """Keep only failure/error lines, deduping consecutive repeats and capping size."""
    kept: list[str] = []
    previous: str | None = None
    for line in text.splitlines():
        if not _INTERESTING_RE.search(line):
            continue
        stripped = line.rstrip()
        if stripped == previous:
            continue
        previous = stripped
        kept.append(stripped)
        if len(kept) >= _MAX_LINES:
            kept.append(f"... (truncated at {_MAX_LINES} lines)")
            break
    return "\n".join(kept)


def _fetch_and_write(task_name: str, task_id: str, dest_dir: Path) -> TaskLogs:
    safe = _safe_filename(task_name)
    full_path = dest_dir / f"{safe}.log"
    sanitized_path = dest_dir / f"{safe}.failures.txt"
    url = ARTIFACT_URL.format(task_id=task_id)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        full_path.write_text(resp.text)
        sanitized = sanitize_log(resp.text)
        sanitized_path.write_text(
            sanitized if sanitized else f"(no failure lines matched in {url})\n"
        )
    except requests.exceptions.RequestException as exc:
        logger.warning("Failed to download log for %s (%s): %s", task_name, url, exc)
        note = f"(failed to download {url}: {exc})\n"
        full_path.write_text(note)
        sanitized_path.write_text(note)
    return TaskLogs(sanitized=sanitized_path, full=full_path)


async def download_failure_logs(
    failure_tasks: dict[str, str], dest_dir: Path
) -> dict[str, TaskLogs]:
    """Download and sanitize each task's log concurrently.

    Returns a mapping of task name to its :class:`TaskLogs`.
    """
    names = list(failure_tasks)
    logs = await asyncio.gather(
        *(
            asyncio.to_thread(_fetch_and_write, name, failure_tasks[name], dest_dir)
            for name in names
        )
    )
    return dict(zip(names, logs))
