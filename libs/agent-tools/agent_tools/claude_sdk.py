"""claude-agent-sdk adapter for framework-neutral tool definitions.

The ONLY module in agent-tools that imports claude-agent-sdk. Wraps a list of
:class:`~agent_tools.registry.ToolDefinition` into an in-process MCP server.
Requires the ``claude-sdk`` optional extra.
"""

from __future__ import annotations

import json

from claude_agent_sdk import create_sdk_mcp_server
from claude_agent_sdk import tool as sdk_tool

from agent_tools.registry import ToolDefinition, ToolError, tool_name_for


def _text(content: str) -> dict:
    """Wrap plain text in the MCP tool-result content shape the SDK expects."""
    return {"content": [{"type": "text", "text": content}]}


def _jtext(obj) -> dict:
    """Serialise an object to pretty JSON inside MCP text content."""
    return _text(json.dumps(obj, indent=2, default=str))


def _make_tool(defn: ToolDefinition, ctx, prefix_namespace: bool):
    mcp_name = tool_name_for(defn.dotted) if prefix_namespace else defn.name

    @sdk_tool(mcp_name, defn.description, defn.input_schema)
    async def run(args):
        try:
            result = await defn.handler(ctx, **args)
        except ToolError as e:
            payload = e.payload if e.payload is not None else {"error": str(e)}
            return {**_jtext(payload), "is_error": True}
        # Handlers return plain data; str is shown verbatim, everything else as JSON.
        return _text(result) if isinstance(result, str) else _jtext(result)

    return run


def build_sdk_server(
    name: str,
    ctx,
    tools: list[ToolDefinition],
    *,
    version: str = "0.1.0",
    prefix_namespace: bool = False,
):
    """Build a claude-agent-sdk ``McpSdkServerConfig`` from tool definitions.

    ``ctx`` is passed as each handler's first argument. ``prefix_namespace``
    names the MCP tools ``<namespace>_<name>`` (used by the shared ``actions``
    server, where one server hosts multiple domains); otherwise the tool name is
    the function name (per-domain servers like ``bugzilla``/``firefox``).
    """
    return create_sdk_mcp_server(
        name=name,
        version=version,
        tools=[_make_tool(d, ctx, prefix_namespace) for d in tools],
    )
