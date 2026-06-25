"""Tests for the Bugzilla read tools."""

from unittest.mock import patch

import pytest
import requests
from agent_tools import bugzilla
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolError
from mcp.types import ListToolsRequest


def _ctx() -> BugzillaContext:
    return BugzillaContext(api_url="https://bz.example", api_key="k")


class FakeBugzilla:
    """Stand-in for libmozdata's Bugzilla.

    Captures the handler kwargs at construction and, on ``wait()``, replays
    canned payloads through them (or raises a configured error) — mirroring the
    real handler-based ``get_data().wait()`` flow.
    """

    bugs: list[dict] = []
    comments: list[dict] = []
    attachments: list[dict] = []
    error: requests.HTTPError | None = None

    def __init__(self, bugids=None, attachmentids=None, **kwargs):
        self.bugids = bugids
        self.attachmentids = attachmentids
        self.kwargs = kwargs

    def get_data(self):
        return self

    def wait(self):
        if FakeBugzilla.error is not None:
            raise FakeBugzilla.error
        bughandler = self.kwargs.get("bughandler")
        if bughandler:
            for bug in FakeBugzilla.bugs:
                bughandler(bug)
        commenthandler = self.kwargs.get("commenthandler")
        if commenthandler:
            for bug in FakeBugzilla.bugs or [{"id": int(self.bugids[0])}]:
                commenthandler({"comments": FakeBugzilla.comments}, str(bug["id"]))
        attachmenthandler = self.kwargs.get("attachmenthandler")
        if attachmenthandler:
            if self.attachmentids is not None:
                attachmenthandler(FakeBugzilla.attachments)
            else:
                attachmenthandler(FakeBugzilla.attachments, self.bugids[0])
        return self


@pytest.fixture(autouse=True)
def reset_fake():
    FakeBugzilla.bugs = []
    FakeBugzilla.comments = []
    FakeBugzilla.attachments = []
    FakeBugzilla.error = None
    yield


async def _list(server):
    return (
        await server.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )
    ).root.tools


async def test_exposes_read_only_tools():
    config = build_sdk_server("bugzilla", _ctx(), bugzilla.TOOLS)
    assert config["type"] == "sdk"
    tools = await _list(config["instance"])
    assert {t.name for t in tools} == {
        "search_bugs",
        "get_bugs",
        "get_bug_comments",
        "get_bug_attachments",
        "download_attachment",
    }


async def test_search_bugs_returns_data():
    FakeBugzilla.bugs = [{"id": 1}, {"id": 2}]
    with patch.object(bugzilla, "Bugzilla", FakeBugzilla):
        result = await bugzilla.search_bugs(_ctx(), params={"id": "1,2"})
    assert result == {"count": 2, "bugs": [{"id": 1}, {"id": 2}]}


async def test_get_bugs_reports_inaccessible():
    FakeBugzilla.bugs = [{"id": 1}]
    with patch.object(bugzilla, "Bugzilla", FakeBugzilla):
        result = await bugzilla.get_bugs(_ctx(), ids=[1, 2])
    assert result["count"] == 1
    assert result["bugs"] == [{"id": 1}]
    assert result["inaccessible"] == [2]


async def test_get_bug_comments_returns_comments():
    FakeBugzilla.comments = [{"id": 10, "text": "hi"}]
    with patch.object(bugzilla, "Bugzilla", FakeBugzilla):
        result = await bugzilla.get_bug_comments(_ctx(), bug_id=1)
    assert result == {
        "bug_id": 1,
        "count": 1,
        "comments": [{"id": 10, "text": "hi"}],
    }


async def test_get_bug_attachments_returns_attachments():
    FakeBugzilla.attachments = [{"id": 5, "file_name": "a.txt"}]
    with patch.object(bugzilla, "Bugzilla", FakeBugzilla):
        result = await bugzilla.get_bug_attachments(_ctx(), bug_id=1)
    assert result == {
        "bug_id": 1,
        "count": 1,
        "attachments": [{"id": 5, "file_name": "a.txt"}],
    }


async def test_download_attachment_writes_file(tmp_path):
    import base64

    raw = b"hello"
    FakeBugzilla.attachments = [
        {
            "id": 5,
            "data": base64.b64encode(raw).decode(),
            "file_name": "a.txt",
            "content_type": "text/plain",
        }
    ]
    dest = tmp_path / "a.txt"
    with patch.object(bugzilla, "Bugzilla", FakeBugzilla):
        result = await bugzilla.download_attachment(
            _ctx(), attachment_id=5, dest_path=str(dest)
        )
    assert dest.read_bytes() == raw
    assert result["size_bytes"] == len(raw)
    assert result["file_name"] == "a.txt"


async def test_download_attachment_not_found():
    FakeBugzilla.attachments = []
    with patch.object(bugzilla, "Bugzilla", FakeBugzilla):
        with pytest.raises(ToolError) as ei:
            await bugzilla.download_attachment(
                _ctx(), attachment_id=5, dest_path="/tmp/nope"
            )
    assert ei.value.payload["error"] == "attachment_not_found"


async def test_search_bugs_raises_tool_error_on_http_failure():
    resp = requests.Response()
    resp.status_code = 403
    resp._content = b'{"code": 102, "message": "nope"}'
    FakeBugzilla.error = requests.HTTPError(response=resp)
    with patch.object(bugzilla, "Bugzilla", FakeBugzilla):
        with pytest.raises(ToolError) as ei:
            await bugzilla.search_bugs(_ctx(), params={})
    assert ei.value.payload["error"] == "access_denied"
    assert ei.value.payload["code"] == 102
