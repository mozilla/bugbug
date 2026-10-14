"""Tests for the recording side of the Slack actions."""

import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import slack
from hackbot_runtime.actions.recorder import ActionsRecorder


async def test_post_message_records_action():
    rec = ActionsRecorder()
    confirmation = await slack.post_message(
        rec,
        channel="#sheriff-notifications",
        text="a test regressed",
        reasoning="sheriffs decide on the backout",
    )
    assert confirmation == (
        f"Recorded slack.post_message (ID: {rec.list_actions()[0]['action_id']})."
    )
    assert rec.actions == [
        {
            "type": "slack.post_message",
            "params": {"channel": "#sheriff-notifications", "text": "a test regressed"},
            "reasoning": "sheriffs decide on the backout",
        }
    ]


@pytest.mark.parametrize("channel,text", [("", "hi"), ("#c", "")])
async def test_post_message_rejects_blank_arguments(channel, text):
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        await slack.post_message(rec, channel=channel, text=text, reasoning="why")
    assert rec.actions == []


def test_record_message_supports_a_ref_for_later_reference():
    rec = ActionsRecorder()
    action = slack.record_message(rec, "sheriffs", "backout recommended", ref="notice")
    assert action["ref"] == "notice"
    assert action["params"]["channel"] == "sheriffs"
    assert rec.actions[0]["ref"] == "notice"


def test_tools_are_exposed_under_the_slack_namespace():
    assert [t.dotted for t in slack.TOOLS] == ["slack.post_message"]


# --- blocks ---


def test_blocks_are_recorded_alongside_the_text():
    rec = ActionsRecorder()
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "*bug 1*"}}]
    action = slack.record_message(rec, "#triage", "bug 1 triaged", blocks=blocks)
    assert action["params"]["blocks"] == blocks
    # The fallback text is not replaced by the layout.
    assert action["params"]["text"] == "bug 1 triaged"


def test_a_message_with_no_blocks_records_no_blocks_key():
    rec = ActionsRecorder()
    action = slack.record_message(rec, "#c", "plain notification")
    assert "blocks" not in action["params"]


def test_blocks_alone_are_enough_to_record_a_message():
    rec = ActionsRecorder()
    blocks = [{"type": "divider"}]
    action = slack.record_message(rec, "#c", None, blocks=blocks)
    assert action["params"]["blocks"] == blocks


def test_a_message_with_neither_text_nor_blocks_is_refused():
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        slack.record_message(rec, "#c", None)
    assert rec.actions == []
