"""Agent-facing tools for inspecting and retracting recorded actions."""

from __future__ import annotations

from typing import Annotated

from agent_tools.registry import tool, tools_in
from pydantic import Field

from hackbot_runtime.actions.recorder import ActionsRecorder


def _table_cell(value: str | None) -> str:
    """Format one value for a Markdown table cell."""
    if value is None:
        return ""
    return "<br>".join(value.splitlines()).replace("|", r"\|")


@tool
async def list_actions(recorder: ActionsRecorder) -> str:
    """List the actions currently proposed by this agent run.

    Returns a Markdown table with each action's ID, type, and reasoning.
    """
    actions = recorder.list_actions()
    if not actions:
        return "No recorded actions."

    rows = ["| ID | Action | Reasoning |", "| --- | --- | --- |"]
    rows.extend(
        f"| {_table_cell(action['action_id'])} "
        f"| {_table_cell(action['type'])} "
        f"| {_table_cell(action.get('reasoning'))} |"
        for action in actions
    )
    return "\n".join(rows)


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
