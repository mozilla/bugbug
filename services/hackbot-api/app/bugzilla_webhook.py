"""Detection and deduplication helpers for Bugzilla needinfo webhooks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class BugzillaNeedinfoEvent:
    """A qualifying needinfo request extracted from a BMO webhook payload."""

    bug_id: int
    flag_id: int
    dedupe_key: str


def _dedupe_key(bug_id: int, flag_id: int, event: dict) -> str:
    """Return a stable identity for retries of one Bugzilla modification."""
    encoded = json.dumps(
        {"bug_id": bug_id, "flag_id": flag_id, "event": event},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def detect_needinfo_request(
    payload: dict, *, bot_login: str
) -> BugzillaNeedinfoEvent | None:
    """Extract a new, public, bot-directed ``needinfo?`` request.

    BMO represents a new request in a bug modification's changes as
    ``{"field": "flag.needinfo", "added": "? (<login>)"}`` and includes the
    corresponding flag (with its ID) in ``bug.flags``. The routing key is
    deliberately not checked because one update may change multiple fields.
    """
    if not bot_login:
        return None

    event = payload.get("event") or {}
    bug = payload.get("bug") or {}

    if event.get("action") != "modify" or event.get("target") != "bug":
        return None
    if bug.get("is_private") is not False:
        return None

    # Ignore Hackbot's own flag changes, so answering cannot retrigger a run.
    actor_login = (event.get("user") or {}).get("login")
    if not actor_login or actor_login == bot_login:
        return None

    expected_added = f"? ({bot_login})"
    if not any(
        change.get("field") == "flag.needinfo" and change.get("added") == expected_added
        for change in event.get("changes") or ()
    ):
        return None

    bug_id = bug.get("id")
    if not bug_id:
        return None

    matching_flags = [
        flag
        for flag in bug.get("flags") or ()
        if flag.get("name") == "needinfo"
        and flag.get("value") == "?"
        and isinstance(flag.get("requestee"), dict)
        and flag["requestee"].get("login") == bot_login
    ]
    if not matching_flags:
        return None
    flag_id = matching_flags[0].get("id")
    if not flag_id:
        return None

    return BugzillaNeedinfoEvent(
        bug_id=bug_id,
        flag_id=flag_id,
        dedupe_key=_dedupe_key(bug_id, flag_id, event),
    )
