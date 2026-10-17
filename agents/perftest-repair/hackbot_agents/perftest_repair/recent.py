"""Whether a bug still fails: its recent failures in Treeherder's intermittent data.

This is the data behind the Intermittent Failures ("OrangeFactor") graph. A bug with
no failures in the last week is skipped without starting a session: if it is gone it
stays quiet, and if it comes back the robot comments again and triggers a new run.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import httpx

log = logging.getLogger(__name__)

FAILURES_BY_BUG_URL = "https://treeherder.mozilla.org/api/failuresbybug/"
RECENT_DAYS = 7
_TIMEOUT_SECONDS = 60
_HEADERS = {"User-Agent": "hackbot-perftest-repair"}


async def recent_failures(bug: int, *, days: int = RECENT_DAYS) -> int | None:
    """Failures classified against ``bug`` in the last ``days``, on every tree.

    ``None`` when Treeherder cannot answer: the caller then runs anyway, so an
    outage never drops a bug that is still failing.
    """
    today = date.today()
    params = {
        "startday": (today - timedelta(days=days)).isoformat(),
        "endday": today.isoformat(),
        "tree": "all",
        "bug": bug,
    }
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_SECONDS, headers=_HEADERS
        ) as client:
            response = await client.get(FAILURES_BY_BUG_URL, params=params)
            response.raise_for_status()
            failures = response.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("could not read recent failures for bug %s: %s", bug, e)
        return None
    if not isinstance(failures, list):
        log.warning("unexpected failuresbybug response for bug %s", bug)
        return None
    return len(failures)
