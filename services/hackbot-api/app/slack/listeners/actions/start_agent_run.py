"""A click on a button that offers to start an agent run."""

import logging
from typing import Any
from uuid import UUID

from hackbot_client import HackbotClient
from pydantic import AliasChoices, BaseModel, Field
from slack_bolt.context.ack.async_ack import AsyncAck
from slack_bolt.context.async_context import AsyncBoltContext
from slack_bolt.context.respond.async_respond import AsyncRespond

from app.config import settings

log = logging.getLogger(__name__)


class StartAgentRunValue(BaseModel):
    """What a button that starts an agent run carries."""

    agent_name: str
    # A message keeps its buttons clickable, so the values posted
    # before the key was renamed from `params` are still out there.
    # FIXME: This alias can be removed after a few months, when we know
    # all messages are old enough. Target date: 2027-02-01.
    inputs: dict[str, Any] = Field(
        default={}, validation_alias=AliasChoices("inputs", "params")
    )
    dedupe_key: str = Field(min_length=1)
    # A run whose pending actions will be applied before the new run starts.
    apply_run_id: UUID | None = None


def _run_url(run_id: UUID) -> str:
    return f"{settings.hackbot_ui_url}/runs/{run_id}"


def _started_note(user: str | None, agent_name: str, run_id: UUID) -> str:
    who = f"<@{user}>" if user else "Someone"
    return f":check-mark-green: {who} started a <{_run_url(run_id)}|{agent_name} run>"


def _replace_clicked_button(
    blocks: list[dict], block_id: str | None, action_id: str, note: str
) -> list[dict] | None:
    """``blocks`` with the clicked button swapped for ``note``, or None if absent.

    Only the clicked button goes: any other button in the same row is a
    different offer and stays clickable, with the note placed under that row.
    """
    note_block = {"type": "context", "elements": [{"type": "mrkdwn", "text": note}]}
    for i, block in enumerate(blocks):
        if block.get("type") != "actions" or block.get("block_id") != block_id:
            continue
        elements = block.get("elements") or []
        remaining = [e for e in elements if e.get("action_id") != action_id]
        if len(remaining) == len(elements):
            return None
        if remaining:
            replacement = [{**block, "elements": remaining}, note_block]
        else:
            replacement = [note_block]
        return blocks[:i] + replacement + blocks[i + 1 :]
    return None


async def _mark_button_used(
    respond: AsyncRespond,
    body: dict,
    action: dict,
    note: str,
    logger: logging.Logger,
) -> None:
    """Show on the message that its one-shot button was used.

    Best effort: the run has started by now, so an update Slack refuses is
    logged, and the dedupe key still stops a second run from a stale button.
    """
    message = body.get("message") or {}
    blocks = _replace_clicked_button(
        message.get("blocks") or [], action.get("block_id"), action["action_id"], note
    )
    if blocks is None:
        logger.warning(
            "Clicked button '%s' not found in its message; left unchanged",
            action["action_id"],
        )
        return

    response = await respond(
        text=f"{message.get('text', '')}\n{note}".strip(),
        blocks=blocks,
        replace_original=True,
    )
    # An error from Slack is returned, not raised.
    if response.status_code != 200:
        logger.error(
            "Failed to mark button '%s' as used: HTTP %s %s",
            action["action_id"],
            response.status_code,
            response.body,
        )


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

    if value.apply_run_id:
        actions = await client.apply_actions(value.apply_run_id)
        if not actions.all_applied:
            logger.error(
                "Slack click by '%s' failed to apply run %s's actions: %s",
                user,
                value.apply_run_id,
                actions.failure_summary,
            )
            await respond(
                text=(
                    ":warning: Could not apply the actions of the run that was "
                    "supposed to be approved by this click. No new agent run was started."
                ),
                response_type="ephemeral",
                replace_original=False,
            )
            await ack()
            return

    run = await client.trigger_run(
        value.agent_name, value.inputs, dedupe_key=value.dedupe_key
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
    else:
        await _mark_button_used(
            respond,
            body,
            action,
            _started_note(user, value.agent_name, run.run_id),
            logger,
        )

    await ack()
