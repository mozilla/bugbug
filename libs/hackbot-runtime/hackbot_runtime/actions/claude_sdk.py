"""Build the claude-agent-sdk ``actions`` MCP server from recordable actions.

Thin wrapper over agent-tools' generic adapter: the ``ActionsRecorder`` is the
tool context, and tools are namespace-prefixed (one ``actions`` server hosts
every domain). Requires the ``claude-sdk`` optional extra.
"""

from __future__ import annotations

from pathlib import Path

from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import tool_name_for

from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions import bugzilla as _bugzilla
from hackbot_runtime.actions.recorder import ActionsRecorder


def actions_server_for(
    recorder: ActionsRecorder | None,
    types: list[str] | None = None,
    *,
    fallback_artifacts_dir: Path = Path("artifacts"),
):
    """Return ``(recorder, sdk_server)`` for the enabled recordable actions.

    ``recorder=None`` creates a local recorder that copies attachments under
    ``fallback_artifacts_dir`` (standalone/script runs with no uploader).
    ``types`` selects a subset by dotted id (e.g. ``bugzilla.update_bug``);
    ``None`` exposes all.
    """
    if recorder is None:
        recorder = ActionsRecorder(artifacts_dir=fallback_artifacts_dir)
    tools = _bugzilla.TOOLS
    if types is not None:
        wanted = set(types)
        tools = [t for t in tools if t.dotted in wanted]
    return recorder, build_sdk_server(
        ACTIONS_SERVER_NAME, recorder, tools, prefix_namespace=True
    )


def actions_to_tool_names(types: list[str]) -> list[str]:
    """claude-agent-sdk tool ids for the given action types.

    e.g. ``"bugzilla.update_bug"`` -> ``"mcp__actions__bugzilla_update_bug"``.
    Kept beside ``actions_server_for`` so the ids stay in sync with the server it
    builds (same server name + tool-name mapping).
    """
    return [f"mcp__{ACTIONS_SERVER_NAME}__{tool_name_for(t)}" for t in types]
