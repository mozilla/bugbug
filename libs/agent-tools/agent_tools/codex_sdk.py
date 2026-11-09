"""Codex adapter for framework-neutral tool definitions.

Codex can't run tools in-process, so this wraps a list of
:class:`~agent_tools.registry.ToolDefinition` into an MCP server that the agent
serves over HTTP on loopback. Requires the ``codex-sdk`` optional extra.
"""

import asyncio
import contextlib
import json
import logging
import socket
from collections.abc import AsyncIterator, Generator

import uvicorn
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from agent_tools.registry import ToolDefinition, ToolError, tool_name_for

DEFAULT_TOOL_TIMEOUT_SEC = 7200


def _text(content: str) -> list[types.TextContent]:
    """Wrap plain text in the MCP tool-result content shape the SDK expects."""
    return [types.TextContent(type="text", text=content)]


def _jtext(obj) -> list[types.TextContent]:
    """Serialise an object to pretty JSON inside MCP text content."""
    return _text(json.dumps(obj, indent=2, default=str))


class _UvicornServer(uvicorn.Server):
    # Let the agent handle shutdown signals instead of Uvicorn.
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


class CodexMcpServer:
    """Hold an MCP server and the settings Codex needs to connect to it."""

    def __init__(
        self,
        server: Server,
        *,
        tool_timeout_sec: float = DEFAULT_TOOL_TIMEOUT_SEC,
    ) -> None:
        self.server = server
        self.name = server.name
        self.tool_timeout_sec = tool_timeout_sec
        # The address is set after the HTTP server starts.
        self.url: str | None = None

    @contextlib.asynccontextmanager
    async def serve(self) -> AsyncIterator["CodexMcpServer"]:
        """Start the HTTP server and stop it when the ``async with`` block ends."""
        logging.getLogger("mcp.server").setLevel(logging.WARNING)
        manager = StreamableHTTPSessionManager(
            app=self.server, stateless=True, json_response=True
        )
        with socket.create_server(("127.0.0.1", 0)) as sock:
            port = sock.getsockname()[1]
            async with manager.run():
                uv = _UvicornServer(
                    uvicorn.Config(
                        manager.handle_request,
                        interface="asgi3",
                        lifespan="off",
                        log_level="warning",
                    )
                )
                serving = asyncio.create_task(uv.serve(sockets=[sock]))
                self.url = f"http://127.0.0.1:{port}/mcp"
                try:
                    yield self
                finally:
                    self.url = None
                    uv.should_exit = True
                    await serving


def build_mcp_server(
    name: str,
    ctx,
    tools: list[ToolDefinition],
    *,
    version: str = "0.1.0",
    prefix_namespace: bool = False,
    instructions: str | None = None,
    tool_timeout_sec: float = DEFAULT_TOOL_TIMEOUT_SEC,
) -> CodexMcpServer:
    """Build a :class:`CodexMcpServer` from tool definitions.

    ``ctx`` is passed as each handler's first argument. ``prefix_namespace``
    names the MCP tools ``<namespace>_<name>`` (used by the shared ``actions``
    server, where one server hosts multiple domains); otherwise the tool name is
    the function name (per-domain servers like ``bugzilla``/``firefox``).
    ``instructions`` is sent to the model once, when the server connects, for
    guidance shared by all of its tools.
    """
    by_name = {
        tool_name_for(d.dotted) if prefix_namespace else d.name: d for d in tools
    }
    server = Server(name, version=version, instructions=instructions)

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=n, description=d.description, inputSchema=d.input_schema)
            for n, d in by_name.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        defn = by_name.get(name)
        if defn is None:
            raise ValueError(f"Unknown tool: {name}")
        try:
            result = await defn.handler(ctx, **arguments)
        except ToolError as e:
            payload = e.payload if e.payload is not None else {"error": str(e)}
            return types.CallToolResult(content=_jtext(payload), isError=True)
        return _text(result) if isinstance(result, str) else _jtext(result)

    return CodexMcpServer(server, tool_timeout_sec=tool_timeout_sec)


def mcp_servers_config(*servers: CodexMcpServer) -> dict:
    """Codex ``thread_start(config=...)`` for the running ``servers``."""
    entries = {}
    for s in servers:
        if s.url is None:
            raise RuntimeError(f"MCP server {s.name!r} is not running")
        entries[s.name] = {
            "url": s.url,
            "tool_timeout_sec": s.tool_timeout_sec,
            "default_tools_approval_mode": "approve",
        }
    return {"mcp_servers": entries}
