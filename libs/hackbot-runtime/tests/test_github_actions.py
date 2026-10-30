"""Tests for the GitHub recording tools (PR comments and inline review comments)."""

import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionsRecorder, github

REPO = "mozilla-mobile/firefox-ios"


async def test_add_comment_records_the_pull_request_and_text():
    rec = ActionsRecorder()
    await github.add_comment(
        rec, repo=REPO, pull_number=7, text="  Here is the answer. ", reasoning="r"
    )
    [action] = rec.actions
    assert action["type"] == "github.add_comment"
    assert action["params"] == {
        "repo": REPO,
        "pull_number": 7,
        "text": "Here is the answer.",
    }
    assert action["reasoning"] == "r"


async def test_add_inline_comments_records_one_action_for_all_comments():
    rec = ActionsRecorder()
    await github.add_inline_comments(
        rec,
        repo=REPO,
        pull_number=7,
        comments=[
            {"path": "a.py", "line": 3, "body": "Off by one?"},
            {"path": "b.py", "line": 12, "start_line": 10, "body": "Extract this."},
        ],
        summary="Two things.",
        reasoning="r",
    )
    [action] = rec.actions
    assert action["type"] == "github.add_inline_comments"
    assert action["params"] == {
        "repo": REPO,
        "pull_number": 7,
        "comments": [
            {"path": "a.py", "line": 3, "body": "Off by one?"},
            {"path": "b.py", "line": 12, "start_line": 10, "body": "Extract this."},
        ],
        "summary": "Two things.",
    }


async def test_add_inline_comments_summary_is_optional():
    rec = ActionsRecorder()
    await github.add_inline_comments(
        rec,
        repo=REPO,
        pull_number=7,
        comments=[{"path": "a.py", "line": 3, "body": "Nit."}],
        reasoning="r",
    )
    assert "summary" not in rec.actions[0]["params"]


@pytest.mark.parametrize(
    ("repo", "pull_number"),
    [("firefox-ios", 7), ("mozilla-mobile/firefox-ios/x", 7), (REPO, 0)],
    ids=["no-owner", "extra-segment", "pull-number"],
)
async def test_a_malformed_pull_request_is_refused(repo, pull_number):
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        await github.add_comment(
            rec, repo=repo, pull_number=pull_number, text="x", reasoning="r"
        )
    with pytest.raises(ToolError):
        await github.add_inline_comments(
            rec,
            repo=repo,
            pull_number=pull_number,
            comments=[{"path": "a.py", "line": 1, "body": "x"}],
            reasoning="r",
        )
    assert rec.actions == []


async def test_a_blank_comment_is_refused():
    rec = ActionsRecorder()
    with pytest.raises(ToolError):
        await github.add_comment(rec, repo=REPO, pull_number=7, text=" ", reasoning="r")
    assert rec.actions == []


@pytest.mark.parametrize(
    "comments",
    [
        [],
        [{"path": "a.py", "line": 0, "body": "x"}],
        [{"path": "a.py", "line": 3, "body": " "}],
        [{"path": "", "line": 3, "body": "x"}],
        [{"path": "a.py", "line": 3, "start_line": 3, "body": "x"}],
    ],
    ids=["none", "line-zero", "blank-body", "blank-path", "start-not-before-end"],
)
async def test_invalid_inline_comments_are_refused(comments):
    rec = ActionsRecorder()
    with pytest.raises(ToolError) as exc:
        await github.add_inline_comments(
            rec, repo=REPO, pull_number=7, comments=comments, reasoning="r"
        )
    assert exc.value.payload["details"]
    assert rec.actions == []


def test_tool_schemas():
    tools = {t.name: t for t in github.TOOLS}
    assert set(tools) == {"add_comment", "add_inline_comments"}
    assert set(tools["add_comment"].input_schema["required"]) == {
        "repo",
        "pull_number",
        "text",
        "reasoning",
    }
    inline = tools["add_inline_comments"].input_schema
    assert set(inline["required"]) == {"repo", "pull_number", "comments", "reasoning"}
    assert "summary" in inline["properties"]
