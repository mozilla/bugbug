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
        text="  a test regressed  ",
        reasoning="sheriffs decide on the backout",
    )
    assert confirmation == "Recorded slack.post_message as action-0."
    assert rec.actions == [
        {
            "type": "slack.post_message",
            "params": {"channel": "#sheriff-notifications", "text": "a test regressed"},
            "reasoning": "sheriffs decide on the backout",
        }
    ]


@pytest.mark.parametrize(
    "channel,text", [("", "hi"), ("   ", "hi"), ("#c", ""), ("#c", "  \n ")]
)
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
    assert rec.actions == [action]


def test_tools_are_exposed_under_the_slack_namespace():
    assert [t.dotted for t in slack.TOOLS] == ["slack.post_message"]
