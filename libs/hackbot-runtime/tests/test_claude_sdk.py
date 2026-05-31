"""Tests for the claude-agent-sdk actions adapter (guards issue #1)."""

import mcp.server.lowlevel.server as low
from hackbot_runtime.actions import ActionsRecorder
from hackbot_runtime.actions.claude_sdk import build_actions_sdk_server
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

_ALL = [
    "bugzilla.update_bug",
    "bugzilla.add_comment",
    "bugzilla.add_attachment",
    "bugzilla.create_bug",
]


def _server(recorder):
    config = build_actions_sdk_server(recorder, types=_ALL)
    assert config["type"] == "sdk"
    return config["instance"]


async def _list(srv):
    return (
        await srv.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
    ).root.tools


async def _call(srv, name, arguments):
    return (
        await srv.request_handlers[CallToolRequest](
            CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(name=name, arguments=arguments),
            )
        )
    ).root


def test_returns_lowlevel_server():
    srv = _server(ActionsRecorder())
    assert isinstance(srv, low.Server)
    assert ListToolsRequest in srv.request_handlers
    assert CallToolRequest in srv.request_handlers


async def test_lists_expected_tools_without_recorder():
    srv = _server(ActionsRecorder())
    tools = await _list(srv)
    assert {t.name for t in tools} == {
        "bugzilla_update_bug",
        "bugzilla_add_comment",
        "bugzilla_add_attachment",
        "bugzilla_create_bug",
    }
    for t in tools:
        assert "recorder" not in t.inputSchema.get("properties", {})


async def test_call_records_action():
    recorder = ActionsRecorder()
    srv = _server(recorder)
    result = await _call(
        srv,
        "bugzilla_update_bug",
        {"bug_id": 7, "changes": {"severity": "S2"}, "reasoning": "rule X"},
    )
    assert result.isError is False
    assert recorder.actions[0]["type"] == "bugzilla.update_bug"
    assert recorder.actions[0]["params"] == {
        "bug_id": 7,
        "changes": {"severity": "S2"},
    }


async def test_missing_file_surfaces_is_error():
    srv = _server(ActionsRecorder())
    result = await _call(
        srv,
        "bugzilla_add_attachment",
        {"bug_id": 7, "file_path": "/no/such/file.patch", "reasoning": "r"},
    )
    assert result.isError is True
    text = " ".join(getattr(c, "text", "") for c in result.content)
    assert "file not found" in text
