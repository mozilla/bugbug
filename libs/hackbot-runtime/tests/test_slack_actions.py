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
    assert "slack.post_message (#0)" in confirmation
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


def test_run_link_hook_appends_a_link_to_the_recorded_message():
    rec = ActionsRecorder()
    rec.add_hook(slack.ACTION_TYPE, slack.run_link_hook("abc", "https://hackbot.test/"))
    slack.record_message(rec, "#c", "a test regressed")
    assert rec.actions[0]["params"]["text"] == (
        "a test regressed\n<https://hackbot.test/runs/abc|hackbot run abc>"
    )


def test_run_link_hook_defaults_to_the_hackbot_ui():
    rec = ActionsRecorder()
    rec.add_hook(slack.ACTION_TYPE, slack.run_link_hook("abc"))
    slack.record_message(rec, "#c", "hi")
    assert rec.actions[0]["params"]["text"].endswith(
        f"\n<{slack.HACKBOT_UI_URL}/runs/abc|hackbot run abc>"
    )
