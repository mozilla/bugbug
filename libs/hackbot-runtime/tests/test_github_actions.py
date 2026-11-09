"""Tests for the GitHub recording tools."""

from hackbot_runtime.actions import ActionsRecorder, github, phabricator
from hackbot_runtime.actions.claude_sdk import actions_to_tool_names


async def test_submit_patch_records_title_and_body():
    rec = ActionsRecorder()

    reply = await github.submit_patch(
        rec, title="Fix the thing", body="Details", reasoning="r"
    )

    action = rec.actions[0]
    assert action["type"] == "github.submit_patch"
    assert action["params"] == {"title": "Fix the thing", "body": "Details"}
    assert action["reasoning"] == "r"
    assert reply.startswith("Recorded github.submit_patch (ID: ")


async def test_submit_patch_body_is_optional():
    rec = ActionsRecorder()

    await github.submit_patch(rec, title="Fix", reasoning="r")

    assert rec.actions[0]["params"] == {"title": "Fix", "body": None}


def test_submit_patch_is_exposed_as_an_action_tool():
    assert [t.dotted for t in github.TOOLS] == ["github.submit_patch"]
    assert actions_to_tool_names(["github.submit_patch"]) == [
        "mcp__actions__github_submit_patch"
    ]


def test_github_patch_types_do_not_overlap_phabricator_ones():
    # Each gates building its own artifact in publish_changes.
    assert {"github.submit_patch"} == github.PATCH_ACTION_TYPES
    assert not github.PATCH_ACTION_TYPES & phabricator.PATCH_ACTION_TYPES
