"""A click on a button that offers to start an agent run."""

import logging
from typing import Any

from hackbot_client import HackbotClient
from pydantic import BaseModel
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.async_context import AsyncBoltContext

log = logging.getLogger(__name__)


class StartAgentRunValue(BaseModel):
    """What a button that starts an agent run carries."""

    agent_name: str
    params: dict[str, Any] = {}


async def start_agent_run_callback(
    ack: AsyncAck,
    action: dict,
    body: dict,
    context: AsyncBoltContext,
    logger: logging.Logger,
) -> None:
    """Start the run this button offers.

    FIXME: we trigger the run before we acknowledge, which has a short timeout
    of 3 seconds. We should queue the run and acknowledge immediately, this
    should be fixed with https://github.com/mozilla/bugbug/issues/6468.
    """
    client: HackbotClient = context["hackbot_client"]
    user = (body.get("user") or {}).get("id")
    value = StartAgentRunValue.model_validate_json(action["value"])

    run = await client.trigger_run(value.agent_name, value.params)

    logger.info(
        "Started %s run %s from a Slack click by %s",
        value.agent_name,
        run.run_id,
        user,
    )
    await ack()
