"""Slack interaction payload handling.

The models mirror Slack's payload, so each one can be read against the reference
side by side, and only the fields the receiver actually uses
are declared. Everything else a delivery carries is ignored.

https://docs.slack.dev/reference/interaction-payloads/block_actions-payload
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, Json

log = logging.getLogger(__name__)


class User(BaseModel):
    """Who clicked."""

    id: str
    # Only ever logged, so a delivery without it is still a click worth acting on.
    username: str | None = None


class Channel(BaseModel):
    """Where the message they clicked is."""

    id: str


class Message(BaseModel):
    """The message they clicked, identified for a later ``chat.update``."""

    ts: str


class Action(BaseModel):
    """The element that was clicked."""

    action_id: str
    # `Json` decodes the value the drawing side put on the button and checks it is
    # an object, so a button carrying something else fails here rather than at the
    # first use of it.
    value: Json[dict[str, Any]]


class ButtonClick(BaseModel):
    """A click on one button of a message this app posted.

    Clicks on message elements are the only interaction this app handles. Slack sends
    other types to the same URL (``view_submission`` when a modal is submitted,
    ``block_suggestion`` for a select's options); this app posts nothing that produces
    one, so a delivery carrying one is as unreadable as any other and fails the same
    way.
    """

    type: Literal["block_actions"]
    user: User
    channel: Channel
    message: Message
    # A click reports exactly one action even in a block of several buttons, so an
    # empty list is a payload shape this does not know.
    actions: list[Action] = Field(min_length=1)
    # Slack's two single-use handles: for replying (valid ~30 minutes) and for
    # opening a modal (~3 seconds).
    response_url: str
    trigger_id: str
