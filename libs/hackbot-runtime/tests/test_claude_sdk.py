"""Tests for the actions MCP server (built via agent-tools' adapter)."""

import mcp.server.lowlevel.server as low
from hackbot_runtime.actions import ActionsRecorder
from hackbot_runtime.actions.claude_sdk import actions_server_for
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

_ALL = [
    "bugzilla.update_bug",
    "bugzilla.add_comment",
    "bugzilla.add_attachment",
    "bugzilla.create_bug",
]


def _server(recorder):
    _, config = actions_server_for(recorder, types=_ALL)
    assert config["type"] == "sdk"
    return config["instance"]


async def _list(srv):
    return (
        await srv.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )
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


def test_actions_server_for_creates_fallback_recorder(tmp_path):
    recorder, config = actions_server_for(
        None, types=_ALL, fallback_artifacts_dir=tmp_path
    )
    assert isinstance(recorder, ActionsRecorder)
    assert config["type"] == "sdk"


def test_actions_server_for_reuses_given_recorder():
    given = ActionsRecorder()
    recorder, config = actions_server_for(given, types=_ALL)
    assert recorder is given
    assert config["type"] == "sdk"


async def test_actions_server_for_exposes_selected_tools():
    _, config = actions_server_for(ActionsRecorder(), types=["bugzilla.update_bug"])
    tools = await _list(config["instance"])
    assert {t.name for t in tools} == {"bugzilla_update_bug"}
