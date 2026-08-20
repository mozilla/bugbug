"""Tests for the phabricator recording tools (submit/update patch, comment)."""

import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionsRecorder, phabricator


async def test_submit_records_create_params_only():
    rec = ActionsRecorder()
    await phabricator.submit_patch(
        rec, bug_id=1, title="Fix the thing", reasoning="r", summary="Details"
    )
    action = rec.actions[0]
    assert action["type"] == "phabricator.submit_patch"
    assert action["params"] == {
        "bug_id": 1,
        "title": "Fix the thing",
        "summary": "Details",
    }
    assert "ref" not in action


@pytest.mark.parametrize(
    "header",
    ["Tests", "tests", "Test Plan", "Testplan", "Tested"],
)
async def test_submit_rejects_test_plan_headers(header):
    rec = ActionsRecorder()

    with pytest.raises(ToolError) as exc:
        await phabricator.submit_patch(
            rec,
            bug_id=1,
            title="Fix",
            reasoning="r",
            summary=f"Explanation\n\n{header}: details",
        )

    assert header in str(exc.value)
    assert "Call submit_patch again with that fixed" in str(exc.value)
    assert rec.actions == []


@pytest.mark.parametrize(
    "summary",
    [
        None,
        "",
        "Testing: details",
        "Some Tests: details",
        " Tests: already indented",
    ],
)
async def test_submit_accepts_safe_summary(summary):
    rec = ActionsRecorder()

    await phabricator.submit_patch(
        rec, bug_id=1, title="Fix", reasoning="r", summary=summary
    )

    assert rec.actions[0]["params"]["summary"] == summary


async def test_submit_requires_title():
    rec = ActionsRecorder()
    with pytest.raises(TypeError):
        await phabricator.submit_patch(rec, bug_id=1, reasoning="r")


async def test_submit_ref_is_recorded():
    rec = ActionsRecorder()
    await phabricator.submit_patch(
        rec, bug_id=1, title="Fix", reasoning="r", ref="patch"
    )
    assert rec.actions[0]["ref"] == "patch"


async def test_update_records_only_the_revision_id():
    rec = ActionsRecorder()
    await phabricator.update_patch(rec, revision_id=12345, reasoning="r")
    action = rec.actions[0]
    assert action["type"] == "phabricator.update_patch"
    # Nothing about the revision itself is the agent's to set on an update.
    assert action["params"] == {"revision_id": 12345}


async def test_update_requires_revision_id():
    rec = ActionsRecorder()
    with pytest.raises(TypeError):
        await phabricator.update_patch(rec, reasoning="r")


def test_agent_facing_schemas_are_case_specific():
    """The point of the split: create takes no revision id, update demands one."""
    submit = next(t for t in phabricator.TOOLS if t.name == "submit_patch")
    update = next(t for t in phabricator.TOOLS if t.name == "update_patch")

    assert "revision_id" not in submit.input_schema["properties"]
    assert set(submit.input_schema["required"]) == {"bug_id", "title", "reasoning"}

    # An update carries the revision id and nothing else: the revision's title,
    # summary and bug id stay as they are, and there is no new URL to reference.
    assert set(update.input_schema["required"]) == {"revision_id", "reasoning"}
    assert set(update.input_schema["properties"]) == {"revision_id", "reasoning"}


async def test_add_comment_records_revision_and_text():
    rec = ActionsRecorder()
    await phabricator.add_comment(
        rec, revision_id=42, text="Here is the answer.", reasoning="r"
    )
    action = rec.actions[0]
    assert action["type"] == "phabricator.add_comment"
    assert action["params"]["revision_id"] == 42
    assert action["params"]["text"].startswith("Here is the answer.")
