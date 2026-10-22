"""Apply-side feedback action: sends an agent's problem report to Sentry.

Reports are tagged with the agent name and run ID, fingerprinted on the title so
repeat reports of the same problem group into one Sentry issue, and tagged
``kind=agent_feedback`` to keep them apart from errors.
"""

from typing import Any

import sentry_sdk

from app.action_handlers.base import ActionResult, ApplyContext


class ReportProblemHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        event_id = sentry_sdk.capture_message(
            params["title"],
            level=params["severity"],
            tags={"kind": "agent_feedback", "agent": ctx.agent, "run_id": ctx.run_id},
            fingerprint=["agent-feedback", params["title"]],
            extras={"message": params["message"]},
        )
        return ActionResult.ok({"event_id": event_id})
