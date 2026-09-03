"""Slack interaction payload handling.

The models mirror Slack's payload, so each one can be read against the reference
side by side, and only the fields the receiver actually uses
are declared. Everything else a delivery carries is ignored.

https://docs.slack.dev/reference/interaction-payloads/block_actions-payload
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

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


class ActionValue(BaseModel):
    type: Literal["start_agent_run"]
    agent_name: str
    params: Annotated[dict[str, Any], Field(default_factory=dict)]


class Action(BaseModel):
    """The element that was clicked."""

    action_id: str
    # `Json` decodes the value the drawing side put on the button and checks it is
    # an object, so a button carrying something else fails here rather than at the
    # first use of it.
    value: Json[ActionValue]


class BlockActionsEvent(BaseModel):
    """A click on one button of a message this app posted.

    Clicks on message elements are the only interaction this app handles. Slack sends
    other types to the same URL (``view_submission`` when a modal is submitted,
    ``block_suggestion`` for a select's options); this app posts nothing that produces
    one, so a delivery carrying one is as unreadable as any other and fails the same
    way.
    """

    type: Literal["block_actions"]
    user: User
    channel: Channel | None = None
    message: Message | None = None
    actions: Annotated[list[Action], Field(default_factory=list)]
    response_url: str | None = None
    trigger_id: str
