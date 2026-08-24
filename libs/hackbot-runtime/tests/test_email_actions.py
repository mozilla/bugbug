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
        attach_artifacts=["changes/changes.patch"],
    )
    assert rec.actions == [action]
    assert action == {
        "type": "email.send",
        "params": {
            "to": ["dev@mozilla.com"],
            "subject": "build failure",
            "body_markdown": "# Analysis",
            "attach_artifacts": ["changes/changes.patch"],
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


def test_patch_block_truncates_and_says_so():
    block = email.patch_block("\n".join(f"+line {i}" for i in range(10)), max_lines=3)
    assert block.startswith("```diff\n+line 0\n+line 1\n+line 2\n```")
    assert "truncated to 3 lines" in block
    assert "+line 3" not in block


def test_patch_block_keeps_a_short_patch_whole():
    assert email.patch_block("+one\n+two") == "```diff\n+one\n+two\n```"
