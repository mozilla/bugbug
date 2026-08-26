"""Slack interaction payload handling: parsing a verified delivery into a click.

Takes raw bytes, because the body is form-encoded rather than JSON and those bytes
are already needed for signature verification. Everything after that is pydantic's:
it parses the JSON, checks the interaction is one this app handles, and validates the
fields the receiver reads.

The models mirror Slack's payload rather than flattening it, so each one can be read
against the reference side by side, and only the fields the receiver actually uses
are declared. Everything else a delivery carries is ignored.

**Anything it cannot read raises**, and the 500 is deliberate. A signature-verified
delivery this cannot parse means Slack changed its payload shape or the signing
secret leaked, and someone did press a button that then did nothing. Slack shows
that person an error, which is the honest outcome: the alternative is a silent 200
that lets them believe the click worked. The `ValidationError` names the field that
was wrong, so Sentry gets the reason rather than a stack trace to decipher.

Slack does not retry an interaction (retries are an Events API feature), so the 500
costs no retry storm either.

https://docs.slack.dev/reference/interaction-payloads/block_actions-payload
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from urllib.parse import parse_qs

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


def parse_interaction(raw_body: bytes) -> ButtonClick:
    """The click a raw interaction delivery carries.

    ``.decode`` raises on bytes that are not UTF-8, and ``model_validate_json``
    raises on a payload that is not JSON, is not an object, or is not a readable
    click. None of them can be answered, so none of them return.
    """
    form = parse_qs(raw_body.decode("utf-8"))

    encoded = form.get("payload")
    if not encoded:
        raise ValueError("Slack interaction: body has no payload field")

    return ButtonClick.model_validate_json(encoded[0])
