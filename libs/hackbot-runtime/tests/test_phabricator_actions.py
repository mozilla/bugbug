"""Tests for the phabricator.submit_patch recording tool."""

import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionsRecorder, phabricator


async def test_create_requires_title():
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        await phabricator.submit_patch(rec, bug_id=1, reasoning="r")


async def test_create_records_without_revision_id():
    rec = ActionsRecorder()
    await phabricator.submit_patch(
        rec, bug_id=1, reasoning="r", title="Fix the thing", summary="Details"
    )
    action = rec.actions[0]
    assert action["type"] == "phabricator.submit_patch"
    assert action["params"] == {
        "bug_id": 1,
        "revision_id": None,
        "title": "Fix the thing",
        "summary": "Details",
    }
    assert "ref" not in action


async def test_update_does_not_require_title():
    rec = ActionsRecorder()
    await phabricator.submit_patch(rec, bug_id=1, reasoning="r", revision_id=12345)
    assert rec.actions[0]["params"]["revision_id"] == 12345


async def test_ref_is_recorded():
    rec = ActionsRecorder()
    await phabricator.submit_patch(
        rec, bug_id=1, reasoning="r", title="Fix", ref="patch"
    )
    assert rec.actions[0]["ref"] == "patch"


async def test_add_comment_records_revision_and_text():
    rec = ActionsRecorder()
    await phabricator.add_comment(
        rec, revision_id=42, text="Here is the answer.", reasoning="r"
    )
    action = rec.actions[0]
    assert action["type"] == "phabricator.add_comment"
    assert action["params"]["revision_id"] == 42
    assert action["params"]["text"].startswith("Here is the answer.")


async def test_add_comment_appends_footer():
    rec = ActionsRecorder()
    await phabricator.add_comment(rec, revision_id=1, text="Answer.", reasoning="r")
    assert rec.actions[0]["params"]["text"].endswith(phabricator._COMMENT_FOOTER)
