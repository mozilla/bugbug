"""Tests for the Codex ``actions`` MCP server."""

import contextlib

from hackbot_runtime.actions import ActionsRecorder
from hackbot_runtime.actions.codex_sdk import actions_server_for
from hackbot_runtime.actions.tools import ACTIONS_INSTRUCTIONS
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

_TYPES = ["bugzilla.update_bug", "bugzilla.add_comment"]


@contextlib.asynccontextmanager
async def _session(server):
    async with (
        server.serve(),
        streamable_http_client(server.url) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        yield session, await session.initialize()


async def test_lists_selected_tools():
    _, server = actions_server_for(ActionsRecorder(), types=_TYPES)
    async with _session(server) as (session, _):
        tools = (await session.list_tools()).tools
    assert {t.name for t in tools} == {"bugzilla_update_bug", "bugzilla_add_comment"}


async def test_sends_actions_instructions():
    _, server = actions_server_for(ActionsRecorder(), types=_TYPES)
    async with _session(server) as (_, init):
        assert init.instructions == ACTIONS_INSTRUCTIONS


async def test_call_records_action():
    recorder, server = actions_server_for(ActionsRecorder(), types=_TYPES)
    async with _session(server) as (session, _):
        result = await session.call_tool(
            "bugzilla_update_bug",
            {"bug_id": 7, "changes": {"severity": "S2"}, "reasoning": "rule X"},
        )
    assert result.isError is False
    assert recorder.actions[0]["type"] == "bugzilla.update_bug"
    assert recorder.actions[0]["params"] == {
        "bug_id": 7,
        "changes": {"severity": "S2"},
    }


def test_actions_server_for_creates_fallback_recorder(tmp_path):
    recorder, server = actions_server_for(
        None, types=_TYPES, fallback_artifacts_dir=tmp_path
    )
    assert isinstance(recorder, ActionsRecorder)
    assert server.name == "actions"


def test_actions_server_for_reuses_given_recorder():
    given = ActionsRecorder()
    recorder, _ = actions_server_for(given, types=_TYPES)
    assert recorder is given
