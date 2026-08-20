"""Tests for the record-time limits on what this agent may write to a bug.

The hooks run inside the agent as the action is recorded, so a rejection reaches the
model as a tool error and the action never lands in summary.json — see
hackbot_agents/frontend_triage/hooks.py.
"""

import pytest
from agent_tools.registry import ToolError
from hackbot_agents.frontend_triage.config import ENABLED_ACTION_TYPES
from hackbot_agents.frontend_triage.hooks import add_comment_hook
from hackbot_runtime.actions import ActionsRecorder
from hackbot_runtime.actions.claude_sdk import actions_to_tool_names

BUG = 2014702


def _recorder():
    rec = ActionsRecorder()
    rec.add_hook("bugzilla.add_comment", add_comment_hook(rec, BUG))
    return rec


def _comment(rec, bug_id=BUG, **params):
    return rec.record(
        "bugzilla.add_comment",
        {"bug_id": bug_id, "text": "hi", **params},
        reasoning="rule X",
    )


def test_the_agent_is_given_no_tool_that_changes_a_bug_field():
    # What makes dropping `severity` structural rather than something the prompt asks
    # for: `ENABLED_ACTION_TYPES` decides which tools the actions server exposes at all.
    assert ENABLED_ACTION_TYPES == ["bugzilla.add_comment"]
    tools = actions_to_tool_names(ENABLED_ACTION_TYPES)
    assert not [t for t in tools if "update_bug" in t], tools


def test_a_private_comment_is_refused():
    rec = _recorder()
    with pytest.raises(ToolError):
        _comment(rec, is_private=True)
    assert rec.actions == []


def test_a_public_comment_is_recorded():
    rec = _recorder()
    _comment(rec, is_private=False)
    assert rec.actions[0]["type"] == "bugzilla.add_comment"


def test_a_comment_against_another_bug_is_refused():
    for bug_id in (999, "2014702", None, f"{BUG} "):
        rec = _recorder()
        with pytest.raises(ToolError):
            _comment(rec, bug_id=bug_id)
        assert rec.actions == []


def test_only_one_comment_may_be_recorded():
    # A model told to write "a single brief comment" can still record fifty; the
    # prompt is not what caps this.
    rec = _recorder()
    _comment(rec)
    with pytest.raises(ToolError):
        _comment(rec)
    assert len(rec.actions) == 1


def test_a_refused_comment_does_not_use_up_the_allowance():
    # The count is taken from what was actually recorded, so a rejected attempt
    # leaves the agent free to retry with a corrected call.
    rec = _recorder()
    with pytest.raises(ToolError):
        _comment(rec, is_private=True)
    _comment(rec)
    assert len(rec.actions) == 1
