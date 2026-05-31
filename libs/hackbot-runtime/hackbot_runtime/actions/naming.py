"""Shared naming for the actions MCP server.

Kept dependency-light (no framework imports) so both the runtime adapter
and agent-side config can derive identical tool names from one place.
"""

ACTIONS_SERVER_NAME = "actions"


def tool_name_for(action_type: str) -> str:
    """Map an action type to its MCP tool name.

    ``"bugzilla.update_bug"`` -> ``"bugzilla_update_bug"``.
    """
    return action_type.replace(".", "_")
