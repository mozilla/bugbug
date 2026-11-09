"""Tests for the MCP server adapter."""

import contextlib
import json
import socket
from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

import pytest
from agent_tools.mcp_server import build_mcp_server, serve
from agent_tools.registry import ToolError, tool, tools_in
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import Field


@dataclass
class _Ctx:
    value: int


@tool
async def sample_tool(
    ctx: _Ctx,
    bug_id: Annotated[int, Field(description="The bug id.")],
    note: Annotated[str, Field(description="A note.")] = "x",
) -> dict:
    """Sample tool docstring."""
    if bug_id < 0:
        raise ToolError("bad id", payload={"error": "negative", "bug_id": bug_id})
    return {"bug_id": bug_id, "note": note, "value": ctx.value}


_TOOLS = tools_in(__name__)


@contextlib.asynccontextmanager
async def _session(server):
    async with (
        serve(server) as url,
        streamable_http_client(url) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        yield session, await session.initialize()


async def test_serve_yields_loopback_url():
    server = build_mcp_server("sample", _Ctx(value=1), _TOOLS)
    async with serve(server) as url:
        assert url.startswith("http://127.0.0.1:")
        assert url.endswith("/mcp")


async def test_serve_releases_port():
    server = build_mcp_server("sample", _Ctx(value=1), _TOOLS)
    async with serve(server) as url:
        port = urlsplit(url).port
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


async def test_sends_name_and_instructions():
    server = build_mcp_server(
        "sample", _Ctx(value=1), _TOOLS, instructions="Sample instructions."
    )
    async with _session(server) as (_, init):
        assert init.serverInfo.name == "sample"
        assert init.instructions == "Sample instructions."


async def test_lists_tools():
    server = build_mcp_server("sample", _Ctx(value=1), _TOOLS)
    async with _session(server) as (session, _):
        tools = (await session.list_tools()).tools
    assert [(t.name, t.description) for t in tools] == [
        ("sample_tool", "Sample tool docstring.")
    ]


async def test_call_passes_ctx():
    server = build_mcp_server("sample", _Ctx(value=42), _TOOLS)
    async with _session(server) as (session, _):
        result = await session.call_tool("sample_tool", {"bug_id": 7})
    assert result.isError is False
    assert json.loads(result.content[0].text) == {
        "bug_id": 7,
        "note": "x",
        "value": 42,
    }


async def test_tool_error_returns_error_result():
    server = build_mcp_server("sample", _Ctx(value=1), _TOOLS)
    async with _session(server) as (session, _):
        result = await session.call_tool("sample_tool", {"bug_id": -1})
    assert result.isError is True
    assert json.loads(result.content[0].text) == {"error": "negative", "bug_id": -1}


async def test_invalid_arguments_return_error_result():
    server = build_mcp_server("sample", _Ctx(value=1), _TOOLS)
    async with _session(server) as (session, _):
        result = await session.call_tool("sample_tool", {"bug_id": "not-a-number"})
    assert result.isError is True
    assert "bug_id" in result.content[0].text


async def test_prefix_namespace():
    server = build_mcp_server("sample", _Ctx(value=1), _TOOLS, prefix_namespace=True)
    async with _session(server) as (session, _):
        names = {t.name for t in (await session.list_tools()).tools}
    assert names == {"test_mcp_server_sample_tool"}
