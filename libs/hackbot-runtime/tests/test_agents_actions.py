"""Tests for the recording side of agent chaining."""

import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import agents
from hackbot_runtime.actions.claude_sdk import actions_to_tool_names
from hackbot_runtime.actions.recorder import ActionsRecorder


def _recorder(*allowed):
    rec = ActionsRecorder()
    agents.allow_agents(rec, allowed)
    return rec


async def test_start_run_records_an_allowed_agent():
    rec = _recorder("next-agent")
    await agents.start_run(rec, agent=" next-agent ", reasoning="worth continuing")
    assert rec.actions == [
        {
            "type": "agents.start_run",
            "params": {"agent": "next-agent"},
            "reasoning": "worth continuing",
        }
    ]


async def test_start_run_refuses_an_agent_that_was_not_allowed():
    rec = _recorder("next-agent", "other-agent")
    with pytest.raises(ToolError, match="allowed: next-agent, other-agent"):
        await agents.start_run(rec, agent="bug-fix", reasoning="why")
    assert rec.actions == []


@pytest.mark.parametrize("agent", ["next-agent", " "])
async def test_a_run_that_allowed_nothing_starts_nothing(agent):
    rec = ActionsRecorder()
    with pytest.raises(ToolError, match="allowed: none"):
        await agents.start_run(rec, agent=agent, reasoning="why")
    assert rec.actions == []


async def test_the_allowlist_belongs_to_one_recorder():
    _recorder("next-agent")
    with pytest.raises(ToolError):
        await agents.start_run(ActionsRecorder(), agent="next-agent", reasoning="w")


def test_start_run_is_exposed_under_the_agents_namespace():
    assert [t.dotted for t in agents.TOOLS] == ["agents.start_run"]
    assert actions_to_tool_names(["agents.start_run"]) == [
        "mcp__actions__agents_start_run"
    ]
