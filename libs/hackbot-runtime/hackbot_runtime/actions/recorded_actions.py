"""Agent-facing tools for inspecting and retracting recorded actions."""

from __future__ import annotations

from typing import Annotated

from agent_tools.registry import tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder


@tool
async def list_actions(recorder: ActionsRecorder) -> list[dict]:
    """List every action currently proposed by this agent run.

    Returns each action's stable in-run ID and its complete recorded payload,
    including parameters, reasoning, references, and attachment metadata. Use
    this when earlier action details are no longer present in your context or
    before deciding whether a proposal needs to be retracted.
    """
    return recorder.list_actions()


@tool
async def remove_action(
    recorder: ActionsRecorder,
    action_id: Annotated[
        str,
        Field(
            description="ID returned when the action was recorded or by list_actions."
        ),
    ],
) -> dict:
    """Retract one proposed action from this agent run.

    The removed action will not appear in the final run summary and cannot be
    applied. This operation accepts exactly one action ID and has no cascade or
    force mode.
    """
    return recorder.remove_action(action_id)


TOOLS = tools_in(__name__)
