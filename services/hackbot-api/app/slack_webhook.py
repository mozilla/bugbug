"""Slack interaction payload handling: parsing a verified delivery into a click.

Turns a delivery into either a :class:`ButtonClick` or None, leaving the route in
``app/routers/slack.py`` to decide what to do with it.

Takes raw bytes, because the body is form-encoded rather than JSON and those bytes
are already needed for signature verification. Returns None rather than raising on
anything it cannot understand, because the route answers such a delivery 200. Both
are explained in ``docs/hackbot/api.md``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

log = logging.getLogger(__name__)

# Only clicks on message elements are handled here. Slack sends other interaction
# types to the same URL (`view_submission` when a modal is submitted,
# `block_suggestion` for a select's options), which are ignored until something
# records a button that needs them.
BLOCK_ACTIONS = "block_actions"

# The `v` a button's `value` must carry: an envelope around the button's args,
# `{"v": 1, "args": {...}}`, so a click on a button posted before a shape change
# reports a version this does not know and is dropped rather than read with the
# wrong meaning. Buttons outlive deploys, since a message stays clickable for as
# long as it is in the channel's history. Whatever draws the first button writes
# this envelope.
SUPPORTED_VALUE_VERSION = 1


@dataclass(frozen=True)
class ButtonClick:
    """A click on one button of a message this app posted.

    ``kind`` is the button's Slack ``action_id``, the name the side that drew the
    button gave it, and ``args`` is what that side put on the button. Everything
    else identifies the click: who, where, on which message, and the two
    single-use handles Slack provides for replying (``response_url``, valid ~30
    minutes) and for opening a modal (``trigger_id``, valid ~3 seconds).
    """

    kind: str
    args: dict[str, Any]
    user_id: str
    user_name: str | None
    team_id: str | None
    channel_id: str | None
    message_ts: str | None
    response_url: str | None
    trigger_id: str | None


def _decode_value(raw: str | None) -> dict[str, Any] | None:
    """The args off a button's ``value``, or None if it is not one of ours."""
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except ValueError:
        log.warning("Slack interaction: button value is not JSON")
        return None
    if not isinstance(decoded, dict) or decoded.get("v") != SUPPORTED_VALUE_VERSION:
        log.warning(
            "Slack interaction: unsupported button value version %r",
            (decoded or {}).get("v") if isinstance(decoded, dict) else None,
        )
        return None
    args = decoded.get("args")
    return args if isinstance(args, dict) else {}


def parse_payload(payload: dict[str, Any]) -> ButtonClick | None:
    """Turn an interaction payload into a :class:`ButtonClick`, or None.

    None covers every payload this cannot act on: another interaction type, a
    click carrying no action, or a button whose value did not come from a version
    of the recording side this understands. Each is logged, since a button that
    silently does nothing is indistinguishable from a broken receiver.
    """
    kind_of_payload = payload.get("type")
    if kind_of_payload != BLOCK_ACTIONS:
        log.info("Ignoring Slack interaction of type %r", kind_of_payload)
        return None

    actions = payload.get("actions") or []
    # A click reports exactly one action even in a block of several buttons, so
    # anything past the first would be a payload shape this does not know.
    action = actions[0] if actions else None
    if not isinstance(action, dict) or not action.get("action_id"):
        log.warning("Ignoring Slack %s delivery with no action", BLOCK_ACTIONS)
        return None

    args = _decode_value(action.get("value"))
    if args is None:
        return None

    user = payload.get("user") or {}
    if not user.get("id"):
        # Every real click names its user; without one there is nobody to
        # authorize, so this is a payload to drop rather than guess at.
        log.warning("Ignoring Slack %s delivery with no user", BLOCK_ACTIONS)
        return None

    return ButtonClick(
        kind=action["action_id"],
        args=args,
        user_id=user["id"],
        user_name=user.get("username") or user.get("name"),
        team_id=(payload.get("team") or {}).get("id"),
        channel_id=(payload.get("channel") or {}).get("id"),
        message_ts=(payload.get("message") or {}).get("ts"),
        response_url=payload.get("response_url"),
        trigger_id=payload.get("trigger_id"),
    )


def parse_interaction(raw_body: bytes) -> ButtonClick | None:
    """Parse a raw interaction delivery: form body, then ``payload`` JSON."""
    try:
        form = parse_qs(raw_body.decode("utf-8"))
    except UnicodeDecodeError:
        log.warning("Slack interaction: body is not UTF-8")
        return None

    encoded = form.get("payload")
    if not encoded:
        log.warning("Slack interaction: body has no payload field")
        return None

    try:
        payload = json.loads(encoded[0])
    except ValueError:
        log.warning("Slack interaction: payload is not JSON")
        return None
    if not isinstance(payload, dict):
        log.warning("Slack interaction: payload is not an object")
        return None

    return parse_payload(payload)
