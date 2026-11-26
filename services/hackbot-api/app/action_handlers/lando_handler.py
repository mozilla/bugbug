"""Apply-side Lando action: back out a landed commit."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from lando_client import LandoClient

from app.action_handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> LandoClient:
    return LandoClient()


class BackoutHandler:
    """Applies ``lando.backout``: Lando reverts the commit on the repo's head."""

    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        lando_repo = params["lando_repo"]
        commit = params["commit"]
        try:
            client = _client()
            job_id = await client.revert_commit(lando_repo, commit)
        except Exception as exc:
            log.exception("Failed to back out %s from %s", commit, lando_repo)
            return ActionResult.failed(str(exc))

        return ActionResult.ok(
            {"job_id": job_id, "url": client.automation_job_url(job_id)}
        )
