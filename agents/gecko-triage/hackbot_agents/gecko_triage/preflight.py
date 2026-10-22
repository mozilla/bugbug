"""Stop a run before it costs anything, on a bug the rules say to leave alone.

The skips run in code rather than in a ruleset, because a ruleset is advisory and
the model can outrank it. They run before the checkout, at zero turns and zero cost.

A bug whose state could not be read fails the run (`require_state`): without it no check
can run, and a failed run is retried by BugBot. A restricted bug is skipped: the broker
withholds it and lists it as `inaccessible`, and `fetch_state` marks the state
`restricted`. BugBot never sends one, since it filters security bugs.
"""

from typing import TYPE_CHECKING

from .config import (
    PEOPLE,
    TEAM,
    TERMS,
    UNSET_FIELD_VALUE,
)
from .teamconfig import scope_for

if TYPE_CHECKING:
    from .state import TriageState


class BugUnreadable(RuntimeError):
    pass


def require_state(state: TriageState | None) -> TriageState:
    """The bug's state, or an error that fails the run.

    A failed run is retried by BugBot; a skip would be a successful run and use up
    the bug's dedupe key, leaving it untriaged until it changes.
    """
    if state is None:
        raise BugUnreadable("the bug could not be read; failing so the run is retried")
    return state


CLOSED_STATUSES = frozenset({"RESOLVED", "VERIFIED", "CLOSED"})
SECURITY_KEYWORD_PREFIXES = ("sec-", "csectype-", "security")


def is_security(bug: dict) -> bool:
    return any(
        isinstance(k, str) and k.startswith(SECURITY_KEYWORD_PREFIXES)
        for k in bug.get("keywords") or ()
    )


def is_meta(bug: dict) -> bool:
    if TERMS.keywords.meta in (bug.get("keywords") or ()):
        return True
    summary = bug.get("summary")
    return isinstance(summary, str) and bool(TERMS.meta_summary.search(summary))


def is_qa(bug: dict) -> bool:
    """A QA whiteboard, or a reporter who is QA."""
    if (bug.get(TERMS.fields.qa_whiteboard) or "").strip():
        return True
    return is_qa_reporter(bug)


def is_qa_reporter(bug: dict) -> bool:
    """A listed QA account, or a QA mark in the reporter's real name."""
    creator = bug.get("creator")
    if isinstance(creator, str) and creator.lower() in PEOPLE.qa_accounts:
        return True
    detail = bug.get("creator_detail") or {}
    real_name = detail.get("real_name") if isinstance(detail, dict) else None
    return isinstance(real_name, str) and any(
        m in real_name for m in PEOPLE.qa_name_markers
    )


def is_employee(bug: dict) -> bool:
    creator = bug.get("creator")
    return isinstance(creator, str) and creator.lower().endswith(PEOPLE.employee_domain)


def is_developer_reporter(bug: dict) -> bool:
    """Staff and not QA, judged from the reporter alone."""
    return is_employee(bug) and not is_qa_reporter(bug)


def reporter_audience(bug: dict) -> str:
    """Who the comment is for: `qa`, `developer` (staff, not QA) or `reporter`."""
    if is_qa(bug):
        return "qa"
    return "developer" if is_employee(bug) else "reporter"


def skip_reason(state: TriageState) -> str | None:
    """Why this bug is skipped, or None to triage it."""
    if state.restricted:
        return "restricted bug (withheld by the broker)"
    bug = state.bug

    key = f"{bug.get('product') or ''} :: {bug.get('component') or ''}"
    if key not in {c.key for c in scope_for(TEAM, bug)}:
        return f"component {key!r} is out of scope"
    if TEAM is None and bug.get("product") not in TERMS.no_team.products:
        return f"product {bug.get('product')!r} is outside the no-team products"
    if is_meta(bug):
        return "meta bug"
    status = bug.get("status")
    if status in CLOSED_STATUSES:
        resolution = bug.get("resolution") or ""
        return f"already {status} {resolution}".rstrip()
    if bug.get("groups"):
        return "restricted bug (non-empty groups)"
    if is_security(bug):
        return "security bug"
    if TERMS.whiteboard.skip in state.whiteboard:
        return f"whiteboard carries {TERMS.whiteboard.skip}"
    # Staff file their own bugs, but an untriaged one still needs severity and
    # priority, so only a bug that already has a severity is left to them.
    if (
        reporter_audience(bug) == "developer"
        and bug.get("severity", UNSET_FIELD_VALUE) != UNSET_FIELD_VALUE
    ):
        return f"filed by a Mozilla employee who is not QA ({bug.get('creator')})"
    # A developer or a person's triage owns the bug; triage has nothing to add.
    if state.assignee:
        return f"assigned to {state.assignee}, who owns the bug"
    if state.bot_has_last_word and not state.closable_since_newest_comment:
        return (
            "gecko-triage or a bot alias wrote the newest comment and nothing that "
            "changes the analysis happened after it"
        )
    return None
