"""Apply-side feedback action: sends an agent's problem report to Sentry.

Reports go to the agents' Sentry project (``AGENTS_SENTRY_DSN``), beside agent
crashes, with an empty scope so none of hackbot-api's own context is attached.
They are tagged with the agent name and run ID, fingerprinted on the title so
repeat reports of the same problem group into one Sentry issue, and tagged
``kind=agent_feedback`` to keep them apart from crashes.
"""

from functools import cache
from typing import Any

import sentry_sdk

from app.action_handlers.base import ActionResult, ApplyContext
from app.config import settings


@cache
def _client() -> sentry_sdk.Client:
    return sentry_sdk.Client(
        dsn=settings.agents_sentry_dsn, environment=settings.environment
    )


class ReportProblemHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        event_id = _client().capture_event(
            {
                "message": params["title"],
                "level": params["severity"],
                "tags": {
                    "kind": "agent_feedback",
                    "agent": ctx.agent,
                    "run_id": ctx.run_id,
                },
                "fingerprint": ["agent-feedback", params["title"]],
                "contexts": {"feedback": {"message": params["message"]}},
            },
            scope=sentry_sdk.Scope(),
        )
        return ActionResult.ok({"event_id": event_id})
