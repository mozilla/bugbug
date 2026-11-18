"""MCP server adapter for framework-neutral tool definitions.

For agent harnesses that only call tools on MCP servers (e.g. Codex), this
wraps a list of :class:`~agent_tools.registry.ToolDefinition` into a FastMCP
server that the agent serves over HTTP on loopback. Requires the
``mcp-server`` optional extra.
"""

import asyncio
import contextlib
import functools
import json
import logging
import socket
from collections.abc import AsyncIterator

import uvicorn
from fastmcp import FastMCP
from fastmcp.tools import Tool, ToolResult

from agent_tools.registry import ToolDefinition, ToolError, tool_name_for


def _json(obj) -> str:
    """Serialise an object to pretty JSON."""
    return json.dumps(obj, indent=2, default=str)


def _make_tool(defn: ToolDefinition, ctx, prefix_namespace: bool) -> Tool:
    mcp_name = tool_name_for(defn.dotted) if prefix_namespace else defn.name

    @functools.wraps(defn.handler)
    async def run(*args, **kwargs):
        try:
            result = await defn.handler(*args, **kwargs)
        except ToolError as e:
            payload = e.payload if e.payload is not None else {"error": str(e)}
            return ToolResult(content=_json(payload), is_error=True)
        return result if isinstance(result, str) else _json(result)

    return Tool.from_function(
        functools.partial(run, ctx),
        name=mcp_name,
        description=defn.description,
        output_schema=None,
    )


def build_mcp_server(
    name: str,
    ctx,
    tools: list[ToolDefinition],
    *,
    version: str = "0.1.0",
    prefix_namespace: bool = False,
    instructions: str | None = None,
) -> FastMCP:
    """Build a FastMCP server from tool definitions.

    ``ctx`` is passed as each handler's first argument. ``prefix_namespace``
    names the MCP tools ``<namespace>_<name>`` (used by the shared ``actions``
    server, where one server hosts multiple domains); otherwise the tool name is
    the function name (per-domain servers like ``bugzilla``/``firefox``).
    ``instructions`` is sent to the model once, when the server connects, for
    guidance shared by all of its tools.
    """
    return FastMCP(
        name,
        version=version,
        instructions=instructions,
        tools=[_make_tool(d, ctx, prefix_namespace) for d in tools],
    )


@contextlib.asynccontextmanager
async def serve(server: FastMCP) -> AsyncIterator[str]:
    """Run ``server`` over local HTTP and yield its URL.

    Stop the server when the ``async with`` block ends. Use Uvicorn directly
    so we can stop it cleanly from code.
    """
    logging.getLogger("mcp.server").setLevel(logging.WARNING)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    with socket.create_server(("127.0.0.1", 0)) as sock:
        port = sock.getsockname()[1]
        uv = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        serving = asyncio.create_task(uv.serve(sockets=[sock]))
        try:
            yield f"http://127.0.0.1:{port}/mcp"
        finally:
            uv.should_exit = True
            await serving
