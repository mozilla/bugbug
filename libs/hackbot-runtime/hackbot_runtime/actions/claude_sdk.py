"""claude-agent-sdk adapter for runtime-registered actions.

Exposes the enabled actions as an in-process MCP server built with the
SDK's own ``tool`` + ``create_sdk_mcp_server`` — guaranteed compatible with
claude-agent-sdk. Other frameworks (LangChain, ...) get their own sibling
adapter as needed; the action registry is shared and framework-neutral.

Requires the ``claude-sdk`` optional extra of hackbot-runtime.
"""

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from hackbot_runtime.actions.naming import ACTIONS_SERVER_NAME, tool_name_for
from hackbot_runtime.actions.recorder import ActionsRecorder
from hackbot_runtime.actions.registry import ActionDefinition, get_actions


def _text(message: str) -> dict:
    """Wrap a message in the MCP tool-result content shape the SDK expects."""
    return {"content": [{"type": "text", "text": message}]}


def _make_tool(defn: ActionDefinition, recorder: ActionsRecorder):
    @tool(tool_name_for(defn.type), defn.description, defn.input_schema)
    async def run(args):
        # The handler returns a short confirmation string. An ActionInputError
        # raised inside it propagates and is rendered by the SDK as an
        # is_error result with the message preserved.
        return _text(await defn.handler(recorder, **args))

    return run


def build_actions_sdk_server(
    recorder: ActionsRecorder,
    types: list[str] | None = None,
    name: str = ACTIONS_SERVER_NAME,
):
    """Return a claude-agent-sdk ``McpSdkServerConfig`` for the enabled actions.

    ``types`` selects a subset of action types; ``None`` exposes all.
    """
    return create_sdk_mcp_server(
        name=name,
        version="0.1.0",
        tools=[_make_tool(defn, recorder) for defn in get_actions(types)],
    )


def actions_server_for(
    recorder: ActionsRecorder | None,
    types: list[str] | None = None,
    *,
    fallback_artifacts_dir: Path = Path("artifacts"),
):
    """Return ``(recorder, sdk_server)`` ready to plug into ``ClaudeAgentOptions``.

    Convenience around :func:`build_actions_sdk_server` that supplies the common
    fallback: standalone/script runs pass ``recorder=None`` and get a local
    recorder that copies attachments under ``fallback_artifacts_dir`` (no
    uploader). Agents running under the runtime pass ``ctx.actions`` and it is
    used as-is.
    """
    if recorder is None:
        recorder = ActionsRecorder(artifacts_dir=fallback_artifacts_dir)
    return recorder, build_actions_sdk_server(recorder, types=types)
