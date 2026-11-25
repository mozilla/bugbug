"""Apply-side agent chaining: starts the recorded agent on the finished run.

Goes through the public API like the webhook receivers, so the new run is
validated, deduplicated and started exactly like any other. The dedupe key is
the parent run, so a redelivered completion event cannot start it twice.
"""

from typing import Any

from hackbot_client import RunStatus

from app.action_handlers.base import ActionResult, ApplyContext
from app.config import settings


class StartRunHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        inputs = {"parent_run_id": ctx.run_id}
        if "artifacts" in params:
            inputs["artifacts"] = params["artifacts"]
        run = await ctx.hackbot_client.trigger_run(
            params["agent"],
            inputs,
            dedupe_key=f"parent-{ctx.run_id}",
        )
        if run.status == RunStatus.failed:
            return ActionResult.failed(f"{run.agent} run {run.run_id} failed to start")
        return ActionResult.ok(
            {
                "run_id": str(run.run_id),
                "agent": run.agent,
                "url": f"{settings.hackbot_ui_url}/runs/{run.run_id}",
            }
        )
