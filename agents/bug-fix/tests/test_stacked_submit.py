"""Tests for refusing new revisions from a stacked revision."""

import pytest
from agent_tools.registry import ToolError
from hackbot_agents.bug_fix.agent import reject_stacked_submit
from hackbot_runtime.actions import ActionsRecorder, phabricator


async def test_submit_patch_refused_on_stacked_revision():
    rec = ActionsRecorder()
    rec.add_hook("phabricator.submit_patch", reject_stacked_submit([41, 42]))

    with pytest.raises(ToolError, match="stacked on D41, D42"):
        await phabricator.submit_patch(rec, bug_id=1, title="Fix", reasoning="r")
    assert rec.actions == []
