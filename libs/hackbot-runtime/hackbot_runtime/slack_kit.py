"""Utilities for building the Block Kit payloads Hackbot's Slack messages carry.

https://docs.slack.dev/reference/block-kit/block-elements
"""

import json
from typing import Any

from slack_sdk.models.blocks import ButtonElement, ConfirmObject


def create_start_agent_run_button(
    label: str,
    *,
    agent_name: str,
    params: dict[str, Any],
    dedupe_key: str,
    apply_run_id: str | None = None,
    confirm: ConfirmObject | None = None,
    style: str | None = None,
) -> ButtonElement:
    """A button that starts one ``agent_name`` run, and only ever one.

    ``dedupe_key`` is required, because Slack cannot un-render a button: a client
    still holding the message can click it again, and two readers can click at
    once. The key is what makes the button a one-shot.

    ``apply_run_id`` names a run whose pending actions are applied *before* the
    new run starts, for a button offered on a result still held for review: the
    press is the review, so what it approves has to land before the agent that
    reads it starts reading.

    ``confirm`` is Slack's confirmation dialog, worth one for a button whose run
    writes somewhere public: a click is the whole of the interaction, and there
    is no undo on the other side of it.
    """
    value: dict[str, Any] = {
        "agent_name": agent_name,
        "params": params,
        "dedupe_key": dedupe_key,
    }
    if apply_run_id is not None:
        value["apply_run_id"] = apply_run_id

    return ButtonElement(
        text=label,
        action_id=f"start_agent_run:{agent_name}:{dedupe_key}",
        value=json.dumps(value),
        style=style,
        confirm=confirm,
    )
