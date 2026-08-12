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
        "auto": False,
        "title": "Bug 1 - verify on Linux",
    }
    # No test_paths key at all when none were asked for, so the handler can
    # tell "not narrowed" from "narrowed to nothing".
    assert "test_paths" not in action["params"]
    assert "ref" not in action


async def test_push_ref_is_recorded():
    rec = ActionsRecorder()
    await try_server.push(rec, tasks=["build-linux64/opt"], reasoning="r", ref="try")
    assert rec.actions[0]["ref"] == "try"


async def test_push_strips_blank_task_labels():
    rec = ActionsRecorder()
    await try_server.push(rec, tasks=[" build-linux64/opt ", "", "  "], reasoning="r")
    assert rec.actions[0]["params"]["tasks"] == ["build-linux64/opt"]


async def test_push_records_an_auto_selection():
    rec = ActionsRecorder()
    await try_server.push(rec, auto=True, reasoning="r")

    params = rec.actions[0]["params"]
    assert params["auto"] is True
    assert params["tasks"] is None


async def test_push_refuses_tasks_and_auto_together():
    """Naming labels while asking CI to choose them is contradictory."""
    rec = ActionsRecorder()
    with pytest.raises(ToolError, match="not both"):
        await try_server.push(
            rec, tasks=["build-linux64/opt"], auto=True, reasoning="r"
        )
    assert rec.actions == []


async def test_push_refuses_tests_without_a_selection():
    """Test paths narrow a selection; alone they would run nothing."""
    rec = ActionsRecorder()
    with pytest.raises(ToolError, match="narrows"):
        await try_server.push(rec, tests={"xpcshell": ["dom/base/test"]}, reasoning="r")
    assert rec.actions == []


async def test_push_records_validated_test_paths():
    """Recording is pure: the mapping is checked, never resolved by running code."""
    rec = ActionsRecorder()
    await try_server.push(
        rec,
        auto=True,
        tests={"mochitest-browser-chrome": ["browser/base/test/"]},
        reasoning="r",
    )

    assert rec.actions[0]["params"]["test_paths"] == {
        "mochitest-browser-chrome": ["browser/base/test"]
    }


async def test_push_rejects_a_malformed_test_mapping_before_recording():
    rec = ActionsRecorder()
    with pytest.raises(ToolError, match="not a test suite name"):
        await try_server.push(
            rec,
            auto=True,
            tests={"browser/base/content/test": ["dom/base/test"]},
            reasoning="r",
        )
    assert rec.actions == []


@pytest.mark.parametrize("tasks", [[], ["", "   "]])
async def test_push_refuses_an_empty_selection(tasks):
    """A push with no tasks would run nothing, so it never gets recorded."""
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        await try_server.push(rec, tasks=tasks, reasoning="r")
    assert rec.actions == []


async def test_push_requires_a_selection():
    """`tasks` is optional now that `auto` exists, but one of them is required."""
    rec = ActionsRecorder()
    with pytest.raises(ToolError, match="needs a selection"):
        await try_server.push(rec, reasoning="r")
    assert rec.actions == []


def test_agent_facing_schema():
    push = next(t for t in try_server.TOOLS if t.name == "push")

    # No patch/repo/base arguments: the push is built from the run's own
    # changes, so the agent only chooses what to run. The selection itself is
    # validated in the handler rather than the schema, since "exactly one of
    # tasks/auto" is not expressible here.
    assert set(push.input_schema["required"]) == {"reasoning"}
    assert set(push.input_schema["properties"]) == {
        "tasks",
        "auto",
        "tests",
        "reasoning",
        "title",
        "ref",
    }


# --- validate_test_paths ------------------------------------------------- #


def test_validate_test_paths_normalises_paths():
    cleaned = try_server.validate_test_paths(
        {"mochitest-browser-chrome": [" browser/base/test/ ", "browser/base/test", ""]}
    )

    assert cleaned == {"mochitest-browser-chrome": ["browser/base/test"]}


def test_validate_test_paths_accepts_a_bare_string():
    """An agent naming one path is easy to get as a string rather than a list."""
    assert try_server.validate_test_paths({"xpcshell": "dom/base/test"}) == {
        "xpcshell": ["dom/base/test"]
    }


def test_validate_test_paths_rejects_a_suite_with_no_paths():
    with pytest.raises(ToolError, match="No test paths"):
        try_server.validate_test_paths({"xpcshell": ["  "]})


@pytest.mark.parametrize(
    "key",
    [
        "browser/base/content/test",  # a path in the suite slot
        "test-linux2404-64/opt-mochitest-browser-chrome-1",  # a task label
        "",
        "  ",
        "mochitest browser chrome",  # spaces: not how any suite is named
    ],
)
def test_validate_test_paths_rejects_a_key_that_is_not_a_suite_name(key):
    """Catches the confusions that need no in-tree vocabulary to spot."""
    with pytest.raises(ToolError, match="not a test suite name"):
        try_server.validate_test_paths({key: ["dom/base/test"]})


def test_validate_test_paths_accepts_any_plausible_suite_name():
    """No vendored allowlist: an upstream rename must not block a valid push.

    A name that is not the suite actually running is ignored by mozharness --
    wasteful, not wrong -- which is a better failure than rejecting a real suite
    we had not heard of. `mach try --no-push` is how the agent gets it right.
    """
    assert try_server.validate_test_paths(
        {"some-new-suite-2027": ["dom/base/test"]}
    ) == {"some-new-suite-2027": ["dom/base/test"]}
