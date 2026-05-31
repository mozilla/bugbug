"""Recordable actions for hackbot agents.

The runtime exposes a generic ``ActionsRecorder`` plus a registry of
domain-grouped declarative actions (``bugzilla.update_bug``,
``bugzilla.add_comment``, ...). Per-framework wrappers (MCP today,
LangChain later) wrap the registry without touching the action
declarations themselves.
"""

from hackbot_runtime.actions import bugzilla as _bugzilla
from hackbot_runtime.actions.recorder import ActionsRecorder
from hackbot_runtime.actions.registry import (
    ActionDefinition,
    ActionInputError,
    get_actions,
)

ALL_ACTIONS: list[ActionDefinition] = [*_bugzilla.DEFINITIONS]

__all__ = [
    "ALL_ACTIONS",
    "ActionDefinition",
    "ActionInputError",
    "ActionsRecorder",
    "get_actions",
]
