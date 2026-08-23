"""Detection helpers for Bugzilla needinfo webhooks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BugzillaNeedinfoEvent:
    """A qualifying needinfo request extracted from a BMO webhook payload."""

    bug_id: int
    flag_id: int


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

    event = payload["event"]
    bug = payload["bug"]

    if event["action"] != "modify" or event["target"] != "bug":
        return None
    if bug["is_private"] is not False:
        return None

    # Ignore Hackbot's own flag changes, so answering cannot retrigger a run.
    actor_login = event["user"]["login"]
    if actor_login == bot_login:
        return None

    expected_added = f"? ({bot_login})"
    if not any(
        change["field"] == "flag.needinfo" and change["added"] == expected_added
        for change in event["changes"]
    ):
        return None

    bug_id = bug["id"]

    matching_flags = [
        flag
        for flag in bug["flags"]
        if flag["name"] == "needinfo"
        and flag["value"] == "?"
        and "requestee" in flag
        and flag["requestee"]["login"] == bot_login
    ]
    if not matching_flags:
        return None
    # BMO returns flags ordered by ID, so a newly requested needinfo is the
    # last matching flag in the webhook's current bug snapshot.
    flag_id = matching_flags[-1]["id"]
    if not flag_id:
        return None

    return BugzillaNeedinfoEvent(
        bug_id=bug_id,
        flag_id=flag_id,
    )
