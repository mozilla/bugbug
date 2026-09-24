"""Apply-side handlers for recorded actions."""

from app.action_handlers.base import (
    ActionHandler,
    ActionResult,
    ApplyContext,
)
from app.action_handlers.bugzilla_handler import (
    merge_resolved,
    plan_coalesced_groups,
)
from app.action_handlers.registry import HANDLERS, get_handler

__all__ = [
    "ActionHandler",
    "ActionResult",
    "ApplyContext",
    "HANDLERS",
    "get_handler",
    "merge_resolved",
    "plan_coalesced_groups",
]
