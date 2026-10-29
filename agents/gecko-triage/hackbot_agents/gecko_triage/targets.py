"""The other bugs a run's writes name, checked after the session ends.

The hooks see one action at a time and cannot read Bugzilla, so a write may name any
bug. After the session, one broker read gets the `dupe_of` bug, the `regressed_by`
bugs, the `see_also` bugs, the bugs the comment names and the bugs the run's commit
messages named. The check drops a write on any other bug. It drops the meta bug write on a
`dupe_of` bug that is closed, a security bug or outside the team's scope. When the
read succeeds, it drops a `regressed_by` or `see_also` change that names an unreadable
or restricted bug, and a `see_also` change that names a meta bug. A failed read keeps
those changes, and the run waits for a person (see `restricted_warning`). A duplicate of
a restricted or unreadable bug fails the run, since a public bug must not point at it. A
duplicate of a meta bug also fails the run: the hooks know only the team's meta bugs and
the meta bugs among the duplicate candidates. A comment that names an unreadable or
restricted bug fails the run too, since the comment cannot be dropped in part and must
not point at that bug. So does a comment that spells the number of a restricted bug
that a commit message names, or of a bug that the broker withheld from a read during
the run (see `withheld`). A withheld bug also holds the run for a person. A duplicate of a
bug that was closed with no fix waits for a person (see `dupe_target_warning`).
"""

import re
import sys
from collections.abc import Mapping, Set
from dataclasses import dataclass

from hackbot_runtime import ActionsRecorder, AgentError

from .actions_view import comment_texts, is_bug_id, updates
from .broker_client import broker_session
from .config import SCOPE_KEYS
from .preflight import CLOSED_STATUSES, is_meta, is_security

LINK_FIELDS = ("regressed_by", "see_also")
TARGET_FIELDS = "id,summary,groups,component,product,status,resolution,keywords"
FETCH_TIMEOUT = 60


class DupeTargetRestricted(AgentError):
    pass


class DupeTargetIsMeta(AgentError):
    pass


class CommentNamesRestricted(AgentError):
    pass


@dataclass(frozen=True)
class Dropped:
    """A write the check drops: one field of the action, or the whole action."""

    action_id: str
    reason: str
    field: str | None = None


def dupe_of(actions: list[dict], bug: int) -> int | None:
    """The bug the triaged bug is recorded as a duplicate of, if any."""
    for update in updates(actions, bug):
        if update.changes.get("resolution") == "DUPLICATE":
            return update.changes.get("dupe_of")
    return None


def _linked(changes: dict, field: str) -> list[int]:
    value = changes.get(field)
    items = value.get("add") if isinstance(value, dict) else None
    return [i for i in items or () if is_bug_id(i)]


# The explicit bug references: "bug N", "bugs A and B", "bugs A, B & C", "bug-N",
# "bugN", "bmo#N" and the bug URLs.
_BUG_REF = re.compile(
    r"\b(?:bugs?|bmo)\s*[#-]?\s*"
    r"(\d+(?:(?:\s*[,&/+]\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)\d+)*)"
    r"|show_bug\.cgi\?(?:[^\s#]*&)?id=(\d+)"
    r"|(?:bugzilla\.mozilla\.org|bugzil\.la)/(\d+)\b",
    re.IGNORECASE,
)


def explicit_bug_ids(text: str) -> set[int]:
    """The bugs that ``text`` names with an explicit reference, never a bare number."""
    return {
        int(n)
        for match in _BUG_REF.finditer(text)
        for n in re.findall(r"\d+", match[0])
    }


def comment_bug_ids(actions: list[dict], bug: int) -> set[int]:
    """The other bugs that the recorded comments name with an explicit reference."""
    ids = set().union(*(explicit_bug_ids(t) for t in comment_texts(actions)))
    ids.discard(bug)
    return ids


def spelled_number(number: int) -> re.Pattern:
    """``number`` with no digit next to it, also inside a token or split by separators."""
    return re.compile(r"(?<!\d)" + r"[^0-9A-Za-z]*".join(str(number)) + r"(?!\d)")


def screen_comment(
    actions: list[dict],
    bug: int,
    found: Mapping[int, dict] | None,
    *,
    commit_bugs: Set[int] = frozenset(),
    withheld: Set[int] = frozenset(),
) -> None:
    """Raise :class:`CommentNamesRestricted` when the comment may name a restricted bug.

    ``found`` holds the ``bugs_to_read`` bugs that were read, or is None when the read
    failed or has no broker. ``commit_bugs`` are the bugs the run's commit messages
    named; a restricted one, or any one when the read failed, is forbidden in every
    spelling. ``withheld`` are the bugs the broker withheld during the run, each
    forbidden in every spelling.
    """
    named = comment_bug_ids(actions, bug)
    if named and found is None:
        raise CommentNamesRestricted(
            f"bug {bug}: the broker read for the bugs this comment names failed or is "
            "not configured, so the run cannot check that they are public"
        )
    readable = found or {}
    if hidden := sorted(i for i in named if not _readable(readable, i)):
        raise CommentNamesRestricted(
            f"bug {bug}: the comment names {_bug_list(hidden)}, which is restricted or "
            "could not be read; a public comment must not point at it"
        )
    forbidden = sorted(i for i in commit_bugs - {bug} if not _readable(readable, i))
    for text in comment_texts(actions):
        if spelled := [i for i in forbidden if spelled_number(i).search(text)]:
            raise CommentNamesRestricted(
                f"bug {bug}: the comment contains the number of {_bug_list(spelled)}, "
                "which a commit message names and is restricted or could not be read"
            )
        if spelled := [
            i for i in sorted(withheld - {bug}) if spelled_number(i).search(text)
        ]:
            raise CommentNamesRestricted(
                f"bug {bug}: the comment contains the number of {_bug_list(spelled)}, "
                "which the broker withheld from a read in this run"
            )


def bugs_to_read(
    actions: list[dict], bug: int, commit_bugs: Set[int] = frozenset()
) -> list[int]:
    """The bugs to read after the run.

    The `dupe_of` bug, the `LINK_FIELDS` bugs, the bugs the comment names and
    ``commit_bugs``, the bugs the run's commit messages named.
    """
    ids = comment_bug_ids(actions, bug) | (commit_bugs - {bug})
    for update in updates(actions, bug):
        for field in LINK_FIELDS:
            ids.update(_linked(update.changes, field))
    duplicate = dupe_of(actions, bug)
    if is_bug_id(duplicate):
        ids.add(duplicate)
    return sorted(ids)


def _unsuitable(target: dict | None) -> str | None:
    if not target:
        return "it could not be read"
    if target.get("status") in CLOSED_STATUSES:
        return "it is closed"
    if is_security(target):
        return "it is a security bug"
    key = f"{target.get('product') or ''} :: {target.get('component') or ''}"
    if key not in SCOPE_KEYS:
        return "it is out of the team's scope"
    return None


def _readable(found: Mapping[int, dict], bug: int) -> bool:
    return bug in found and not found[bug].get("groups")


def _bug_list(ids: list[int]) -> str:
    return ", ".join(f"bug {i}" for i in ids)


def screen_writes(
    actions: list[dict], bug: int, found: Mapping[int, dict] | None
) -> list[Dropped]:
    """The writes to drop, given ``found``: the ``bugs_to_read`` bugs that were read.

    ``found`` is None when the read failed or has no broker. Then no `LINK_FIELDS`
    change is dropped: the run waits for a person, see `restricted_warning`.

    Raises :class:`DupeTargetRestricted` when the `dupe_of` bug is restricted or
    unreadable, and :class:`DupeTargetIsMeta` when it is a meta bug.
    """
    read, found = found is not None, found or {}
    duplicate = dupe_of(actions, bug)
    if duplicate is not None and not _readable(found, duplicate):
        raise DupeTargetRestricted(
            f"bug {bug}: the duplicate target bug {duplicate} is restricted or "
            "could not be read; a public bug must not point at it"
        )
    if duplicate is not None and is_meta(found[duplicate]):
        raise DupeTargetIsMeta(
            f"bug {bug}: the duplicate target bug {duplicate} is a meta bug; a bug "
            "blocks a meta bug, it does not duplicate one"
        )
    dropped = []
    for action_id, bug_id, changes in updates(actions):
        if bug_id == bug:
            for field in LINK_FIELDS if read else ():
                linked = _linked(changes, field)
                unread = [i for i in linked if not _readable(found, i)]
                if unread:
                    reason = f"{_bug_list(unread)} could not be read or is restricted"
                elif field == "see_also" and (
                    metas := [i for i in linked if is_meta(found[i])]
                ):
                    reason = f"{_bug_list(metas)} is a meta bug"
                else:
                    continue
                dropped.append(
                    Dropped(
                        action_id,
                        f"the {field} change on bug {bug}, since {reason}",
                        field,
                    )
                )
            continue
        # The model can remove the DUPLICATE change after this write passed its hook.
        if bug_id != duplicate:
            reason = "no recorded duplicate change names it"
        else:
            reason = _unsuitable(found.get(bug_id))
        if reason:
            dropped.append(
                Dropped(action_id, f"the change on bug {bug_id}, since {reason}")
            )
    return dropped


def restricted_warning(
    ids: list[int], found: Mapping[int, dict] | None, unchecked: str | None = None
) -> str | None:
    """Why a person must check the comment: the ``ids`` read that are restricted.

    With a failed read, every id counts. ``unchecked`` says why a read of the run could
    not be checked, which also holds the run. The ids stay in the run's findings, which
    are internal, and never reach a public comment.
    """
    hidden = [i for i in ids if found is None or not _readable(found, i)]
    if not hidden and unchecked is None:
        return None
    parts = [unchecked] if unchecked else []
    if hidden:
        parts.insert(0, f"the run met {_bug_list(hidden)}, restricted or not readable")
        parts.append(
            "a person must check that the comment does not name or describe these bugs"
        )
    else:
        parts.append(
            "a person must check that the comment names or describes no restricted bug"
        )
    return "; ".join(parts)


def dupe_target_warning(
    actions: list[dict], bug: int, found: Mapping[int, dict]
) -> str | None:
    """Why a person must check the duplicate close: its target was closed with no fix.

    Such a target, a duplicate chain too, does not show that this bug is fixed. An
    open target is the normal case and needs no warning. ``found`` holds the `dupe_of`
    bug, since `screen_writes` raises otherwise.
    """
    duplicate = dupe_of(actions, bug)
    if duplicate is None:
        return None
    target = found[duplicate]
    if (
        target.get("status") not in CLOSED_STATUSES
        or target.get("resolution") == "FIXED"
    ):
        return None
    state = f"{target.get('status')} {target.get('resolution') or ''}".strip()
    return (
        f"the duplicate target bug {duplicate} is {state}, not FIXED; a "
        "person must check that the current tree fixes every path this bug reports"
    )


def drop_writes(recorder: ActionsRecorder, dropped: list[Dropped]) -> None:
    """Remove each dropped field, or the whole action when nothing else is left."""
    if not dropped:
        return
    live = {
        copy["action_id"]: action
        for copy, action in zip(recorder.list_actions(), recorder.actions)
    }
    for d in dropped:
        changes = live[d.action_id].get("params", {}).get("changes")
        if d.field is not None and isinstance(changes, dict):
            changes.pop(d.field, None)
            if changes:
                continue
        recorder.remove_action(d.action_id)


async def fetch_bugs(
    bugzilla_mcp_server: Mapping, ids: list[int]
) -> dict[int, dict] | None:
    """The bugs the broker returns, by id; empty when there is nothing to read.

    Never raises: None with no broker url, on any failure, or after FETCH_TIMEOUT
    seconds.
    """
    if not ids:
        return {}
    url = bugzilla_mcp_server.get("url")
    if not url:
        return None
    try:
        async with broker_session(url, FETCH_TIMEOUT) as call:
            res = await call("get_bugs", {"ids": ids, "include_fields": TARGET_FIELDS})
        bugs = res.get("bugs") or []
        wanted = set(ids)
        return {
            b["id"]: b for b in bugs if isinstance(b, dict) and b.get("id") in wanted
        }
    except Exception as e:  # see docstring
        print(
            f"[gecko_triage] reading bugs {ids} failed ({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return None
