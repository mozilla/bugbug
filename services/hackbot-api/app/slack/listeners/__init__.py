"""Every listener this Slack app has, and the one place they are attached.

Split by the kind of interaction Slack calls them, which is how Bolt itself is
organised: ``actions`` for clicks on a message's interactive elements, and, when
there is something to put in them, ``views`` for modal submissions (#6613) and
``commands`` for slash commands. Those two are not directories yet, because an
empty package is not a plan; they slot in beside ``actions`` when the first one
arrives, and `register_listeners` is the only line that has to change.
"""

from slack_bolt.async_app import AsyncApp

from app.slack.listeners import actions


def register_listeners(app: AsyncApp) -> None:
    """Attach every listener to ``app``, once, at construction."""
    actions.register(app)
