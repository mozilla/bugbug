"""Clicks on the buttons that start an agent run or apply a held run's actions."""

import logging
from typing import Any
from uuid import UUID

from hackbot_client import HackbotClient
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, Json, validate_call
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


class SlackUser(BaseModel):
    """User who started a Slack interaction.

    https://docs.slack.dev/reference/objects/user-object/
    """

    id: str | None = None


class BlockActionsPayload(BaseModel):
    """The Slack payload received when a button is clicked.

    https://docs.slack.dev/reference/interaction-payloads/block_actions-payload/
    """

    user: SlackUser | None = None
    message: dict


class ClickedButton(BaseModel):
    """Where a clicked button sits in its message.

    https://docs.slack.dev/reference/interaction-payloads/block_actions-payload/
    """

    action_id: str
    block_id: str


class ButtonAction(ClickedButton):
    """The action that starts an agent run."""

    value: Json[StartAgentRunValue]


class ApplyRunActionsValue(BaseModel):
    """What a button that applies a run's actions, and starts nothing, carries."""

    run_id: UUID


class ApplyRunActionsButton(ClickedButton):
    value: Json[ApplyRunActionsValue]


def _run_url(run_id: UUID) -> str:
    return f"{settings.hackbot_ui_url}/runs/{run_id}"


def _note_block(user: str | None, did: str) -> dict:
    who = f"<@{user}>" if user else "Someone"
    note = f":check-mark-green: {who} {did}"
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": note}]}


def _message_with_note(
    message: dict,
    action: ClickedButton,
    note_block: dict,
    also_remove: frozenset[str] = frozenset(),
) -> dict | None:
    """``message`` with the clicked button swapped for ``note_block``, or None if absent.

    Only the clicked button goes, plus any in ``also_remove``, which names
    buttons the click has made moot: any other button in the same row is a
    different offer and stays clickable, with the note placed under that row.
    """
    blocks = message["blocks"]
    for i, block in enumerate(blocks):
        if block["block_id"] != action.block_id:
            continue
        if block["type"] != "actions":
            log.error(
                "Clicked button '%s' is in a '%s' block, which is not supported yet",
                action.action_id,
                block["type"],
            )
            return None
        elements = block["elements"]
        if not any(e["action_id"] == action.action_id for e in elements):
            log.error(
                "Clicked button '%s' is not in its block's elements, which is unexpected",
                action.action_id,
            )
            return None
        filtered_elements = [
            e
            for e in elements
            if e["action_id"] != action.action_id and e["action_id"] not in also_remove
        ]
        replacement = (
            [{**block, "elements": filtered_elements}, note_block]
            if filtered_elements
            else [note_block]
        )
        return {
            "text": f"{message['text']}\n{note_block['elements'][0]['text']}".strip(),
            "blocks": blocks[:i] + replacement + blocks[i + 1 :],
        }


async def _apply(
    client: HackbotClient,
    run_id: UUID,
    user: str | None,
    respond: AsyncRespond,
    logger: logging.Logger,
) -> bool:
    """Apply ``run_id``'s pending actions, telling the clicker if any did not land."""
    actions = await client.apply_actions(run_id)
    if actions.all_applied:
        return True
    logger.error(
        "Slack click by '%s' failed to apply run %s's actions: %s",
        user,
        run_id,
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
    return False


async def _mark_used(
    body: BlockActionsPayload,
    action: ClickedButton,
    note_block: dict,
    fallback_text: str,
    respond: AsyncRespond,
    logger: logging.Logger,
    also_remove: frozenset[str] = frozenset(),
) -> None:
    """Swap the clicked button for ``note_block``, or tell the clicker privately."""
    user = body.user.id if body.user else None
    updated_message = _message_with_note(body.message, action, note_block, also_remove)
    replaced = False
    if updated_message:
        response = await respond(**updated_message, replace_original=True)
        replaced = response.status_code == 200
        if not replaced:
            logger.error(
                "Failed to mark button '%s' as used: HTTP %s %s",
                action.action_id,
                response.status_code,
                response.body,
            )
    else:
        logger.warning(
            "Slack click by '%s' on button '%s' could not be replaced in its message: %s",
            user,
            action.action_id,
            fallback_text,
        )

    if not replaced:
        response = await respond(
            text=fallback_text,
            response_type="ephemeral",
            replace_original=False,
            unfurl_links=False,
        )
        if response.status_code != 200:
            logger.error(
                "Failed to send ephemeral response to '%s': HTTP %s %s",
                user,
                response.status_code,
                response.body,
            )


@validate_call(config=ConfigDict(arbitrary_types_allowed=True))
async def start_agent_run_callback(
    ack: AsyncAck,
    action: ButtonAction,
    body: BlockActionsPayload,
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
    user = body.user.id if body.user else None
    value = action.value

    if value.apply_run_id and not await _apply(
        client, value.apply_run_id, user, respond, logger
    ):
        await ack()
        return

    run = await client.trigger_run(
        value.agent_name, value.inputs, dedupe_key=value.dedupe_key
    )

    run_url = _run_url(run.run_id)
    await _mark_used(
        body,
        action,
        _note_block(
            user if run.is_new else None,
            f"started a <{run_url}|{value.agent_name} run>",
        ),
        f"A {value.agent_name} run was started: {run_url}",
        respond,
        logger,
        # The click just applied that run, so its post-only button is moot.
        also_remove=(
            frozenset({f"apply_run_actions:{value.apply_run_id}"})
            if value.apply_run_id
            else frozenset()
        ),
    )
    await ack()


@validate_call(config=ConfigDict(arbitrary_types_allowed=True))
async def apply_run_actions_callback(
    ack: AsyncAck,
    action: ApplyRunActionsButton,
    body: BlockActionsPayload,
    context: AsyncBoltContext,
    respond: AsyncRespond,
    logger: logging.Logger,
) -> None:
    """Apply a held run's pending actions on a Slack click, and start nothing.

    The same FIXME as `start_agent_run_callback`: the apply happens before the ack.
    """
    client: HackbotClient = context["hackbot_client"]
    user = body.user.id if body.user else None
    run_id = action.value.run_id

    if await _apply(client, run_id, user, respond, logger):
        run_url = _run_url(run_id)
        await _mark_used(
            body,
            action,
            _note_block(user, f"posted the <{run_url}|analysis>"),
            f"The analysis was posted: {run_url}",
            respond,
            logger,
        )
    await ack()
