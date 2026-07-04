"""Apply-side handlers for recorded actions.

``actions/bugzilla.py`` and ``actions/phabricator.py`` (sibling package) let an
agent *record* an intent into ``summary.json``; the handlers here turn a
recorded action back into a real API call once a run has finished. Kept in the
same library so the set of action types an agent can request and the set this
package knows how to apply never drift apart.
"""

from hackbot_runtime.actions.handlers.base import (
    ActionHandler,
    ActionResult,
    ApplyContext,
)
from hackbot_runtime.actions.handlers.registry import HANDLERS, get_handler

__all__ = [
    "ActionHandler",
    "ActionResult",
    "ApplyContext",
    "HANDLERS",
    "get_handler",
]
