"""Tests for the read-only Phabricator tools."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_tools import phabricator
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.phabricator import PhabricatorContext
from agent_tools.registry import ToolError
from mcp.types import ListToolsRequest


def _ctx(**client_attrs) -> PhabricatorContext:
    """A context whose client answers only the calls a test cares about."""
    client = MagicMock()
    client.revision_url.side_effect = lambda rid: f"https://phab.example.com/D{rid}"
    for name, value in client_attrs.items():
        setattr(client, name, AsyncMock(return_value=value))
    return PhabricatorContext(client=client)


def _inline(
    *,
    phid="PHID-XACT-1",
    comment_id=1,
    comment_phid="PHID-INLN-1",
    date=100,
    content="needs a null check",
    line=10,
    length=1,
    diff_id=9,
    is_done=False,
    reply_to=None,
    author="PHID-USER-1",
) -> dict:
    """A transaction.search 'inline' transaction, shaped as Conduit returns it."""
    return {
        "phid": phid,
        "type": "inline",
        "authorPHID": author,
        "groupID": "group-1",
        "comments": [
            {
                "id": comment_id,
                "phid": comment_phid,
                "version": 1,
                "dateCreated": date,
                "dateModified": date,
                "removed": False,
                "content": {"raw": content},
            }
        ],
        "fields": {
            "diff": {"id": diff_id, "phid": f"PHID-DIFF-{diff_id}"},
            "path": "browser/base/content/browser.js",
            "line": line,
            "length": length,
            "isDone": is_done,
            "replyToCommentPHID": reply_to,
        },
    }


def _general(*, comment_id=2, date=200, content="looks good", author="PHID-USER-2"):
    """A transaction.search 'comment' transaction."""
    return {
        "phid": f"PHID-XACT-{comment_id}",
        "type": "comment",
        "authorPHID": author,
        "groupID": "group-2",
        "comments": [
            {
                "id": comment_id,
                "phid": f"PHID-XCMT-{comment_id}",
                "version": 1,
                "dateCreated": date,
                "dateModified": date,
                "removed": False,
                "content": {"raw": content},
            }
        ],
        "fields": {},
    }


def _revision(revision_id=42, diff_id=9, **fields) -> dict:
    return {
        "id": revision_id,
        "phid": "PHID-DREV-1",
        "fields": {
            "title": "Fix the thing",
            "summary": "A longer explanation.",
            "status": {"name": "Needs Review", "closed": False},
            "authorPHID": "PHID-USER-1",
            "bugzilla.bug-id": "12345",
            "diffID": diff_id,
            "dateCreated": 1,
            "dateModified": 2,
            **fields,
        },
    }


async def _list(server):
    return (
        await server.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )
    ).root.tools


async def test_exposes_read_only_tools():
    config = build_sdk_server("phabricator", _ctx(), phabricator.TOOLS)
    assert config["type"] == "sdk"
    tools = await _list(config["instance"])
    assert {t.name for t in tools} == {
        "get_revision",
        "get_revision_comments",
        "get_revision_diff",
    }


async def test_get_revision_returns_metadata_and_reviewer_names():
    ctx = _ctx(
        search_revision_by_id={
            **_revision(),
            "attachments": {
                "reviewers": {
                    "reviewers": [
                        {
                            "reviewerPHID": "PHID-USER-2",
                            "status": "accepted",
                            "isBlocking": False,
                        },
                        # A review group has no username, only a PHID.
                        {
                            "reviewerPHID": "PHID-PROJ-1",
                            "status": "added",
                            "isBlocking": True,
                        },
                    ]
                }
            },
        },
        search_users={
            "PHID-USER-1": {"username": "author", "real_name": "The Author"},
            "PHID-USER-2": {"username": "reviewer", "real_name": "The Reviewer"},
        },
    )

    result = await phabricator.get_revision(ctx, revision_id=42)

    assert result["revision_id"] == 42
    assert result["title"] == "Fix the thing"
    assert result["status"] == "Needs Review"
    assert result["is_closed"] is False
    assert result["author"] == "author"
    assert result["bug_id"] == "12345"
    assert result["latest_diff_id"] == 9
    assert result["url"] == "https://phab.example.com/D42"
    assert result["reviewers"] == [
        {
            "name": "reviewer",
            "phid": "PHID-USER-2",
            "status": "accepted",
            "is_blocking": False,
        },
        {
            "name": None,
            "phid": "PHID-PROJ-1",
            "status": "added",
            "is_blocking": True,
        },
    ]
    # Reviewers are an attachment, so they must be requested explicitly.
    ctx.client.search_revision_by_id.assert_awaited_once_with(
        42, attachments={"reviewers": True}
    )


async def test_get_revision_raises_when_not_visible():
    ctx = _ctx(search_revision_by_id=None)
    with pytest.raises(ToolError) as ei:
        await phabricator.get_revision(ctx, revision_id=42)
    assert ei.value.payload["error"] == "revision_not_found"


async def test_get_revision_reports_conduit_failure_as_tool_error():
    ctx = _ctx()
    ctx.client.search_revision_by_id = AsyncMock(
        side_effect=RuntimeError("Conduit error ERR-CONDUIT-CORE: nope")
    )
    with pytest.raises(ToolError) as ei:
        await phabricator.get_revision(ctx, revision_id=42)
    assert ei.value.payload["error"] == "phabricator_request_failed"
    assert "looking up D42" in ei.value.payload["while"]


async def test_get_revision_comments_locates_inline_comments():
    ctx = _ctx(
        search_revision_by_id=_revision(diff_id=9),
        # Conduit returns transactions newest first.
        search_transactions=[_general(date=200), _inline(date=100)],
        search_users={
            "PHID-USER-1": {"username": "reviewer", "real_name": "R"},
            "PHID-USER-2": {"username": "author", "real_name": "A"},
        },
    )

    result = await phabricator.get_revision_comments(ctx, revision_id=42)

    assert result["count"] == 2
    assert result["latest_diff_id"] == 9
    # Reordered oldest first.
    inline, general = result["comments"]
    assert inline["type"] == "inline"
    assert inline["author"] == "reviewer"
    assert inline["content"] == "needs a null check"
    assert inline["review_group_id"] == "group-1"
    assert inline["position"] == {
        "path": "browser/base/content/browser.js",
        "start_line": 10,
        "end_line": 10,
        "line_count": 1,
        "diff_id": 9,
        "diff_phid": "PHID-DIFF-9",
        "is_on_latest_diff": True,
        "is_done": False,
        "is_reply": False,
        "reply_to_comment_phid": None,
    }
    assert general["type"] == "comment"
    assert "position" not in general


async def test_inline_length_is_an_inclusive_line_count():
    # length is lineLength + 1, so length 3 at line 10 covers lines 10-12.
    ctx = _ctx(
        search_revision_by_id=_revision(),
        search_transactions=[_inline(line=10, length=3)],
        search_users={},
    )
    position = (await phabricator.get_revision_comments(ctx, revision_id=42))[
        "comments"
    ][0]["position"]
    assert (position["start_line"], position["end_line"]) == (10, 12)
    assert position["line_count"] == 3


async def test_inline_length_floors_at_one_line():
    ctx = _ctx(
        search_revision_by_id=_revision(),
        search_transactions=[_inline(line=10, length=0)],
        search_users={},
    )
    position = (await phabricator.get_revision_comments(ctx, revision_id=42))[
        "comments"
    ][0]["position"]
    assert (position["start_line"], position["end_line"]) == (10, 10)


async def test_inline_comment_on_older_diff_is_flagged():
    ctx = _ctx(
        search_revision_by_id=_revision(diff_id=11),
        search_transactions=[_inline(diff_id=9)],
        search_users={},
    )
    position = (await phabricator.get_revision_comments(ctx, revision_id=42))[
        "comments"
    ][0]["position"]
    assert position["diff_id"] == 9
    assert position["is_on_latest_diff"] is False


async def test_reply_is_linked_to_the_comment_it_answers():
    ctx = _ctx(
        search_revision_by_id=_revision(),
        search_transactions=[_inline(reply_to="PHID-INLN-1", comment_id=5)],
        search_users={},
    )
    position = (await phabricator.get_revision_comments(ctx, revision_id=42))[
        "comments"
    ][0]["position"]
    assert position["is_reply"] is True
    assert position["reply_to_comment_phid"] == "PHID-INLN-1"


async def test_non_comment_transactions_are_skipped():
    ctx = _ctx(
        search_revision_by_id=_revision(),
        search_transactions=[
            {"phid": "PHID-XACT-9", "type": "status", "comments": [], "fields": {}},
            {"phid": "PHID-XACT-8", "type": "reviewers", "comments": [], "fields": {}},
            _general(),
        ],
        search_users={},
    )
    result = await phabricator.get_revision_comments(ctx, revision_id=42)
    assert [c["type"] for c in result["comments"]] == ["comment"]


async def test_only_the_current_version_of_an_edited_comment_is_kept():
    edited = _general()
    edited["comments"] = [
        {
            "id": 2,
            "phid": "PHID-XCMT-2",
            "version": 2,
            "dateCreated": 200,
            "dateModified": 300,
            "removed": False,
            "content": {"raw": "current text"},
        },
        {
            "id": 2,
            "phid": "PHID-XCMT-2",
            "version": 1,
            "dateCreated": 200,
            "dateModified": 200,
            "removed": False,
            "content": {"raw": "original text"},
        },
    ]
    ctx = _ctx(
        search_revision_by_id=_revision(),
        search_transactions=[edited],
        search_users={},
    )
    comment = (await phabricator.get_revision_comments(ctx, revision_id=42))[
        "comments"
    ][0]
    assert comment["content"] == "current text"
    assert comment["was_edited"] is True


async def test_removed_comment_is_reported_without_content():
    removed = _general()
    removed["comments"][0]["removed"] = True
    removed["comments"][0]["content"] = {"raw": ""}
    ctx = _ctx(
        search_revision_by_id=_revision(),
        search_transactions=[removed],
        search_users={},
    )
    comment = (await phabricator.get_revision_comments(ctx, revision_id=42))[
        "comments"
    ][0]
    assert comment["removed"] is True
    assert comment["content"] == ""


async def test_get_revision_comments_filters_by_path():
    other_file = _inline(comment_id=7, date=50)
    other_file["fields"]["path"] = "toolkit/other.js"
    ctx = _ctx(
        search_revision_by_id=_revision(),
        search_transactions=[_inline(), other_file, _general()],
        search_users={},
    )
    result = await phabricator.get_revision_comments(
        ctx, revision_id=42, path="toolkit/other.js"
    )
    assert result["count"] == 1
    assert result["comments"][0]["position"]["path"] == "toolkit/other.js"


async def test_get_revision_diff_defaults_to_the_latest_diff():
    ctx = _ctx(
        search_revision_by_id=_revision(diff_id=11),
        get_raw_diff="diff --git a/f b/f\n",
    )
    result = await phabricator.get_revision_diff(ctx, revision_id=42)
    assert result == {
        "revision_id": 42,
        "diff_id": 11,
        "truncated": False,
        "diff": "diff --git a/f b/f\n",
    }
    ctx.client.get_raw_diff.assert_awaited_once_with(11)


async def test_get_revision_diff_accepts_an_explicit_diff_id():
    ctx = _ctx(get_raw_diff="old diff")
    result = await phabricator.get_revision_diff(ctx, revision_id=42, diff_id=9)
    assert result["diff_id"] == 9
    # An explicit diff id needs no revision lookup.
    ctx.client.search_revision_by_id.assert_not_called()


async def test_get_revision_diff_truncates_a_huge_diff():
    ctx = _ctx(search_revision_by_id=_revision(), get_raw_diff="x" * 500)
    ctx.max_diff_bytes = 100
    result = await phabricator.get_revision_diff(ctx, revision_id=42)
    assert result["truncated"] is True
    assert len(result["diff"]) == 100


async def test_get_revision_diff_errors_when_revision_has_no_diff():
    ctx = _ctx(search_revision_by_id=_revision(diff_id=None))
    with pytest.raises(ToolError) as ei:
        await phabricator.get_revision_diff(ctx, revision_id=42)
    assert ei.value.payload["error"] == "no_diff"
