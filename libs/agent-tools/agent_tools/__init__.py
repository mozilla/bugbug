"""Reusable, framework-neutral agent tools.

Each tool is an async handler decorated with :func:`agent_tools.registry.tool`;
the decorator infers its name, namespace, description and argument schema. A
per-framework adapter (``agent_tools.claude_sdk`` today) turns a module's tools
into a runnable server. Import the submodule you need directly (e.g.
``from agent_tools import bugzilla``) — this ``__init__`` imports no submodules,
so pulling one tool never drags in another's optional dependencies, and the
base package never imports any agent framework.
"""

from agent_tools.registry import ToolDefinition, ToolError, tool, tools_in

__all__ = ["ToolDefinition", "ToolError", "tool", "tools_in"]
