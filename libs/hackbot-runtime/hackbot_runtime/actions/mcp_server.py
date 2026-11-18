"""Build the ``actions`` MCP server from recordable actions.

Thin wrapper over agent-tools' MCP server adapter: the ``ActionsRecorder`` is
the tool context, and tools are namespace-prefixed (one ``actions`` server hosts
every domain). Requires the ``mcp-server`` optional extra.
"""

from pathlib import Path

from agent_tools.mcp_server import build_mcp_server
from fastmcp import FastMCP

from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.recorder import ActionsRecorder
from hackbot_runtime.actions.tools import ACTIONS_INSTRUCTIONS, action_tools


def actions_server_for(
    recorder: ActionsRecorder | None,
    types: list[str] | None = None,
    *,
    fallback_artifacts_dir: Path = Path("artifacts"),
) -> tuple[ActionsRecorder, FastMCP]:
    """Return ``(recorder, server)`` for the enabled recordable actions.

    ``recorder=None`` creates a local recorder that copies attachments under
    ``fallback_artifacts_dir`` (standalone/script runs with no uploader).
    ``types`` selects a subset by dotted id (e.g. ``bugzilla.update_bug``);
    ``None`` exposes all.
    """
    if recorder is None:
        recorder = ActionsRecorder(artifacts_dir=fallback_artifacts_dir)
    return recorder, build_mcp_server(
        ACTIONS_SERVER_NAME,
        recorder,
        action_tools(types),
        prefix_namespace=True,
        instructions=ACTIONS_INSTRUCTIONS,
    )
