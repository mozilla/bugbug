"""Detection and deduplication helpers for Bugzilla needinfo webhooks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class BugzillaNeedinfoEvent:
    """A qualifying needinfo request extracted from a BMO webhook payload."""

    bug_id: int
    dedupe_key: str


def _dedupe_key(bug_id: int, event: dict) -> str:
    """Return a stable identity for retries of one Bugzilla modification."""
    encoded = json.dumps(
        {"bug_id": bug_id, "event": event},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def detect_needinfo_request(
    payload: object, *, bot_login: str
) -> BugzillaNeedinfoEvent | None:
    """Extract a new, public, bot-directed ``needinfo?`` request.

    BMO represents a new request in a bug modification's changes as
    ``{"field": "flag.needinfo", "added": "? (<login>)"}``. The routing key
    is deliberately not checked because one update may change multiple fields.
    """
    if not bot_login or not isinstance(payload, dict):
        return None

    event = payload.get("event")
    bug = payload.get("bug")
    if not isinstance(event, dict) or not isinstance(bug, dict):
        return None

    if event.get("action") != "modify" or event.get("target") != "bug":
        return None
    if bug.get("is_private") is not False:
        return None

    actor_login = event.get("user").get("login")
    if actor_login == bot_login:
        return None

    changes = event.get("changes")
    if not isinstance(changes, list):
        return None

    expected_added = f"? ({bot_login})"
    if not any(
        isinstance(change, dict)
        and change.get("field") == "flag.needinfo"
        and change.get("added") == expected_added
        for change in changes
    ):
        return None

    bug_id = bug["id"]

    return BugzillaNeedinfoEvent(
        bug_id=bug_id,
        dedupe_key=_dedupe_key(bug_id, event),
    )
