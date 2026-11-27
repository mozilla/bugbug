"""Apply-side try-server action: push an already-built patch series to Lando."""

import json
import logging
from functools import lru_cache
from typing import Any

from lando_client import (  # noqa: F401
    LandoClient,
    encode_patch,
    try_task_config,
    try_task_config_patch,
)

from app.action_handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_TRY_PUSH_ARTIFACT_KEY = "changes/try_push.json"

# Which Try repository the push lands on, and the Treeherder repo it shows up
# under. Firefox's plain "try"; a Thunderbird agent would need
# "try-comm-central" here (and a matching Lando permission).
_TRY_REPO_NAME = "try"


@lru_cache(maxsize=1)
def _client() -> LandoClient:
    return LandoClient()


class PushHandler:
    """Applies ``try_server.push``: the run's commits become a try push."""

    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        tasks = params.get("tasks") or []
        auto = bool(params.get("auto"))
        test_paths = params.get("test_paths") or None
        rebuild = params.get("rebuild") or None
        if not tasks and not auto:
            return ActionResult.failed(
                "A try push needs a selection: either `auto` or task labels"
            )

        try:
            raw = await ctx.download_artifact(_TRY_PUSH_ARTIFACT_KEY)
            submission = json.loads(raw)
        except Exception as exc:
            log.exception("Failed to load try push artifact for run %s", ctx.run_id)
            return ActionResult.failed(
                f"Could not load the try push artifact for this run: {exc}"
            )

        try:
            client = _client()
            job_id = await client.submit_try_patches(
                [
                    *submission["patches"],
                    encode_patch(
                        try_task_config_patch(
                            tasks,
                            auto=auto,
                            test_paths=test_paths,
                            rebuild=rebuild,
                        )
                    ),
                ],
                submission["base_commit"],
                base_commit_vcs=submission["base_commit_vcs"],
                patch_format=submission["patch_format"],
                repo_name=_TRY_REPO_NAME,
            )
        except Exception as exc:
            log.exception("Failed to push run %s to try", ctx.run_id)
            return ActionResult.failed(str(exc))

        return ActionResult.ok(
            {
                "job_id": job_id,
                # `url` is the field a `{{actions.<action_id>.url}}` placeholder reads,
                # so it is the one a human would want from a try push.
                "url": client.treeherder_url(job_id, _TRY_REPO_NAME),
                "lando_url": client.job_url(job_id),
                # What was actually selected, so the row records it without a
                # reader having to re-read the recorded params.
                "selection": "auto" if auto else "tasks",
                "tasks": sorted(set(tasks)),
                **({"test_paths": test_paths} if test_paths else {}),
                **({"rebuild": rebuild} if rebuild else {}),
            }
        )
