"""Detection helpers for Bugzilla needinfo webhooks."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

log = logging.getLogger(__name__)


def detect_perf_regression_creation(
    payload: dict, reporters: Sequence[str] = ()
) -> int | None:
    """Route only public, newly-created performance regression bugs.

    With ``reporters``, only bugs filed by one of those accounts are routed.
    Which suites to investigate is decided by the run from the alert itself.
    """
    event = payload.get("event")
    bug = payload.get("bug")
    if not isinstance(event, dict) or not isinstance(bug, dict):
        return None
    if event.get("action") != "create" or event.get("target") != "bug":
        return None
    if bug.get("is_private") is not False:
        return None
    keywords = bug.get("keywords")
    if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
        return None
    if not {"perf-alert", "regression"}.issubset(keywords):
        return None
    if reporters and bug.get("creator") not in reporters:
        return None
    bug_id = bug.get("id")
    return bug_id if type(bug_id) is int and bug_id > 0 else None


@dataclass(frozen=True)
class BugzillaNeedinfoEvent:
    """A qualifying needinfo request extracted from a BMO webhook payload."""

    bug_id: int
    flag_id: int
    comment: str
    user_login: str


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

    # FIXME: Workaround for https://bugzilla.mozilla.org/show_bug.cgi?id=2079991
    # (`changes` keeps only the last needinfo per update), so `added` may not
    # name the bot; it is matched via `bug.flags` below instead.
    if not any(
        change["field"] == "flag.needinfo" and change["added"].startswith("?")
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
    if len(matching_flags) > 1:
        # TODO: Handle all open requests (https://github.com/mozilla/bugbug/issues/6621).
        log.warning(
            "Bug %s has %d open Hackbot needinfo requests (flag IDs: %s); "
            "only the newest will be processed",
            bug_id,
            len(matching_flags),
            [flag["id"] for flag in matching_flags],
        )
    # BMO returns flags ordered by ID, so a newly requested needinfo is the
    # last matching flag in the webhook's current bug snapshot.
    flag_id = matching_flags[-1]["id"]
    if not flag_id:
        return None

    comment = (
        f"Check whether Bugzilla user {actor_login} posted a comment at exactly "
        f"{event['time']}. If one exists, treat it as the developer's request. "
        "A needinfo may be requested without a comment, so use the surrounding "
        "bug context if none exists."
    )
    return BugzillaNeedinfoEvent(
        bug_id=bug_id, flag_id=flag_id, comment=comment, user_login=actor_login
    )
