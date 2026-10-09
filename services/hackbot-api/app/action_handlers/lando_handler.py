"""Apply-side Lando action: push an agent-built backout to a landing repo."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from lando_client import LandoClient, encode_patch

from app.action_handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_BACKOUT_ATTACHMENT = "backout.patch"


@lru_cache(maxsize=1)
def _client() -> LandoClient:
    return LandoClient()


class BackoutHandler:
    """Applies ``lando.backout``: the recorded revert lands on the repo's head."""

    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        key = ctx.artifact_key(_BACKOUT_ATTACHMENT)
        if key is None:
            return ActionResult.failed("The backout action carries no patch")

        try:
            patch = await ctx.download_artifact(key)
        except Exception as exc:
            log.exception("Failed to load backout patch for run %s", ctx.run_id)
            return ActionResult.failed(f"Could not load the backout patch: {exc}")

        lando_repo = params["lando_repo"]
        try:
            client = _client()
            job_id = await client.push_commits(lando_repo, [encode_patch(patch)])
        except Exception as exc:
            log.exception("Failed to back out %s from %s", params["commit"], lando_repo)
            return ActionResult.failed(str(exc))

        return ActionResult.ok(
            {"job_id": job_id, "url": client.automation_job_url(job_id)}
        )
