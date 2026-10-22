"""Tests for the recording side of agent chaining."""

import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import agents
from hackbot_runtime.actions.claude_sdk import actions_to_tool_names
from hackbot_runtime.actions.recorder import ActionsRecorder


async def test_start_run_records_the_agent_to_start():
    rec = ActionsRecorder()
    await agents.start_run(rec, agent=" next-agent ", reasoning="worth continuing")
    assert rec.actions == [
        {
            "type": "agents.start_run",
            "params": {"agent": "next-agent"},
            "reasoning": "worth continuing",
        }
    ]


async def test_start_run_rejects_a_blank_agent():
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        await agents.start_run(rec, agent=" ", reasoning="why")
    assert rec.actions == []


def test_start_run_is_exposed_under_the_agents_namespace():
    assert [t.dotted for t in agents.TOOLS] == ["agents.start_run"]
    assert actions_to_tool_names(["agents.start_run"]) == [
        "mcp__actions__agents_start_run"
    ]
