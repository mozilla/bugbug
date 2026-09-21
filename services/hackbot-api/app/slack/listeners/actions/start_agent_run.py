"""A click on a button that offers to start an agent run."""

import logging
from typing import Any
from uuid import UUID

from hackbot_client import HackbotClient
from pydantic import BaseModel, Field
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.async_context import AsyncBoltContext
from slack_bolt.context.respond.async_respond import AsyncRespond

from app.config import settings

log = logging.getLogger(__name__)


class StartAgentRunValue(BaseModel):
    """What a button that starts an agent run carries."""

    agent_name: str
    params: dict[str, Any] = {}
    dedupe_key: str = Field(min_length=1)


def _run_url(run_id: UUID) -> str:
    return f"{settings.hackbot_ui_url}/runs/{run_id}"


async def start_agent_run_callback(
    ack: AsyncAck,
    action: dict,
    body: dict,
    context: AsyncBoltContext,
    respond: AsyncRespond,
    logger: logging.Logger,
) -> None:
    """Start an agent run based on a Slack click.

    FIXME: we trigger the run before we acknowledge, which has a short timeout
    of 3 seconds. We should queue the run and acknowledge immediately, this
    should be fixed with https://github.com/mozilla/bugbug/issues/6468.
    """
    client: HackbotClient = context["hackbot_client"]
    user = (body.get("user") or {}).get("id")
    value = StartAgentRunValue.model_validate_json(action["value"])

    run = await client.trigger_run(
        value.agent_name, value.params, dedupe_key=value.dedupe_key
    )

    if not run.is_new:
        logger.warning(
            "Slack click by '%s' for key '%r' ignored because a run with the same key run already exists (id=%s)",
            user,
            value.dedupe_key,
            run.run_id,
        )
        await respond(
            text=(
                f"A {value.agent_name} run is already triggered for this "
                f"({run.status.value}): {_run_url(run.run_id)}"
            ),
            response_type="ephemeral",
            replace_original=False,
            unfurl_links=False,
        )

    await ack()
