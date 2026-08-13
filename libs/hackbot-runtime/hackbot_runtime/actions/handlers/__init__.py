"""Apply-side handlers for recorded actions."""

from hackbot_runtime.actions.handlers.base import (
    ActionHandler,
    ActionResult,
    ApplyContext,
)
from hackbot_runtime.actions.handlers.bugzilla_handler import (
    merge_resolved,
    plan_coalesced_groups,
)
from hackbot_runtime.actions.handlers.registry import HANDLERS, get_handler

__all__ = [
    "ActionHandler",
    "ActionResult",
    "ApplyContext",
    "HANDLERS",
    "get_handler",
    "merge_resolved",
    "plan_coalesced_groups",
]
