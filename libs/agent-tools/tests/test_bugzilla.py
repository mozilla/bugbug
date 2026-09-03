"""Tests for the Bugzilla read tools."""

from unittest.mock import MagicMock

import pytest
from agent_tools import bugzilla
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolError
from mcp.types import ListToolsRequest


async def _list(server):
    return (
        await server.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )
    ).root.tools


async def test_exposes_read_only_tools():
    config = build_sdk_server(
        "bugzilla", BugzillaContext(client=MagicMock()), bugzilla.TOOLS
    )
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
    client = MagicMock()
    client.request.return_value = {"bugs": [{"id": 1}, {"id": 2}]}
    result = await bugzilla.search_bugs(
        BugzillaContext(client=client), params={"id": "1,2"}
    )
    assert result == {"count": 2, "bugs": [{"id": 1}, {"id": 2}]}


async def test_search_bugs_raises_tool_error_on_bugsy_failure():
    import bugsy

    client = MagicMock()
    err = bugsy.BugsyException("nope")
    err.code = 102
    client.request.side_effect = err
    with pytest.raises(ToolError) as ei:
        await bugzilla.search_bugs(BugzillaContext(client=client), params={})
    assert ei.value.payload["error"] == "access_denied"
