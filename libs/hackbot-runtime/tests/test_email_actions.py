"""Tests for the recording side of the email action."""

import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import email
from hackbot_runtime.actions.recorder import ActionsRecorder


def test_record_email_records_the_action():
    rec = ActionsRecorder()
    action = email.record_email(
        rec,
        to=["dev@mozilla.com"],
        subject="  build failure  ",
        body_markdown="  # Analysis  ",
        attach_patch=True,
    )
    assert rec.actions == [action]
    assert action == {
        "type": "email.send",
        "params": {
            "to": ["dev@mozilla.com"],
            "subject": "build failure",
            "body_markdown": "# Analysis",
            "attach_patch": True,
        },
        "reasoning": None,
    }


def test_recipients_are_deduped_and_blanks_dropped():
    rec = ActionsRecorder()
    action = email.record_email(
        rec,
        to=[" dev@mozilla.com ", "dev@mozilla.com", "", "author@mozilla.com"],
        subject="s",
        body_markdown="b",
    )
    assert action["params"]["to"] == ["dev@mozilla.com", "author@mozilla.com"]


def test_a_report_concerning_no_individual_still_records():
    # The handler addresses the team; an empty recipient list is not an error.
    rec = ActionsRecorder()
    action = email.record_email(rec, subject="s", body_markdown="b")
    assert action["params"]["to"] == []


@pytest.mark.parametrize(
    "subject,body", [("", "b"), ("  ", "b"), ("s", ""), ("s", " ")]
)
def test_blank_subject_or_body_is_rejected(subject, body):
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        email.record_email(rec, subject=subject, body_markdown=body)
    assert rec.actions == []


def test_demote_headings_nests_agent_prose():
    assert email.demote_headings("# Root\ntext\n## Sub") == "### Root\ntext\n#### Sub"


def test_demote_headings_leaves_fenced_code_alone():
    md = "```\n# not a heading\n```\n# heading"
    assert email.demote_headings(md) == "```\n# not a heading\n```\n### heading"


def test_the_body_can_carry_the_patch_without_attaching_it():
    rec = ActionsRecorder()
    action = email.record_email(
        rec, subject="s", body_markdown=f"```diff\n{email.PATCH_PLACEHOLDER}\n```"
    )
    assert action["params"]["attach_patch"] is False
    assert email.PATCH_PLACEHOLDER in action["params"]["body_markdown"]


def test_a_patch_can_be_attached_without_appearing_in_the_body():
    rec = ActionsRecorder()
    action = email.record_email(rec, subject="s", body_markdown="b", attach_patch=True)
    assert action["params"]["attach_patch"] is True
    assert email.PATCH_PLACEHOLDER not in action["params"]["body_markdown"]


async def test_send_records_the_action():
    rec = ActionsRecorder()
    confirmation = await email.send(
        rec,
        to=[" dev@mozilla.com ", "dev@mozilla.com"],
        subject="  build failure  ",
        body_markdown="  # Analysis  ",
        reasoning="the pusher has to back this out",
    )
    assert "email.send (#0)" in confirmation
    assert rec.actions == [
        {
            "type": "email.send",
            "params": {
                "to": ["dev@mozilla.com"],
                "subject": "build failure",
                "body_markdown": "# Analysis",
                "attach_patch": False,
            },
            "reasoning": "the pusher has to back this out",
        }
    ]


@pytest.mark.parametrize("subject,body", [("", "b"), ("s", "  ")])
async def test_send_rejects_blank_arguments(subject, body):
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        await email.send(
            rec, to=["a@b.c"], subject=subject, body_markdown=body, reasoning="why"
        )
    assert rec.actions == []


def test_tools_are_exposed_under_the_email_namespace():
    assert [t.dotted for t in email.TOOLS] == ["email.send"]
