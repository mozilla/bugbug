"""Tests for the try-server recording tool (push)."""

import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionsRecorder, try_server


async def test_push_records_tasks_and_title():
    rec = ActionsRecorder()
    await try_server.push(
        rec,
        tasks=["build-linux64/opt"],
        reasoning="r",
        title="Bug 1 - verify on Linux",
    )
    action = rec.actions[0]
    assert action["type"] == "try_server.push"
    assert action["params"] == {
        "tasks": ["build-linux64/opt"],
        "title": "Bug 1 - verify on Linux",
    }
    assert "ref" not in action


async def test_push_ref_is_recorded():
    rec = ActionsRecorder()
    await try_server.push(rec, tasks=["build-linux64/opt"], reasoning="r", ref="try")
    assert rec.actions[0]["ref"] == "try"


async def test_push_strips_blank_task_labels():
    rec = ActionsRecorder()
    await try_server.push(rec, tasks=[" build-linux64/opt ", "", "  "], reasoning="r")
    assert rec.actions[0]["params"]["tasks"] == ["build-linux64/opt"]


@pytest.mark.parametrize("tasks", [[], ["", "   "]])
async def test_push_refuses_an_empty_selection(tasks):
    """A push with no tasks would run nothing, so it never gets recorded."""
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        await try_server.push(rec, tasks=tasks, reasoning="r")
    assert rec.actions == []


async def test_push_requires_tasks():
    rec = ActionsRecorder()
    with pytest.raises(TypeError):
        await try_server.push(rec, reasoning="r")


def test_agent_facing_schema():
    push = next(t for t in try_server.TOOLS if t.name == "push")

    # No patch/repo/base arguments: the push is built from the run's own
    # changes, so the agent only chooses which tasks run.
    assert set(push.input_schema["required"]) == {"tasks", "reasoning"}
    assert set(push.input_schema["properties"]) == {
        "tasks",
        "reasoning",
        "title",
        "ref",
    }
