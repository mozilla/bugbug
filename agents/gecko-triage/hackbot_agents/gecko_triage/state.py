"""Triage state, read from the bug itself.

The agent keeps no memory of its own on the bug. Each run reads the whole bug, with all
of its comments, and decides from it the status, what is missing and the next step.
Who asked for what does not matter, because other people also ask for information.

The code answers three questions from the bug:

| Is there anything to do?    | not when the agent wrote the newest comment and nothing that changes the analysis happened after it, unless the bug became closable after it |
| Who does the bug wait for?  | every open needinfo flag, whoever set it                                     |
| May the bug close?          | INCOMPLETE when every open needinfo is on the reporter, who has been quiet for `STALE_NEEDINFO_DAYS` |

The agent's comment is a `BOT_ACCOUNT` comment tagged `AGENT_TAG`, or any comment by a
`BOT_ALIASES` login. The same snapshot feeds the preflight, which skips a bug at zero
cost. It also feeds the hooks, which limit what the agent may write, and the prompt.
"""

import asyncio
import datetime as dt
import sys
from dataclasses import dataclass, field
from functools import cached_property

from claude_agent_sdk import McpServerConfig

from .broker_client import Call, broker_session
from .config import (
    AGENT_TAG,
    BOT_ACCOUNT,
    BOT_ALIASES,
    NEEDINFO_EXPERTS,
    NEEDINFO_FALLBACK,
    STALE_NEEDINFO_DAYS,
    TERMS,
)
from .preflight import is_developer_reporter

# `id` is required: `get_bugs` diffs requested against returned ids and raises KeyError
# without it. `last_change_time_non_bot` is BMO-specific and not in `_default`.
BUG_FIELDS = (
    "id,product,component,summary,status,resolution,keywords,whiteboard,groups,assigned_to,"
    f"creator,creator_detail,{TERMS.fields.qa_whiteboard},cc,flags,severity,priority,"
    "blocks,regressed_by,regressions,see_also,creation_time,last_change_time,last_change_time_non_bot,"
    "cf_crash_signature,type"
)
# Bugzilla's assignee for a bug that nobody owns.
UNASSIGNED = "nobody@mozilla.org"
# BMO automation logins: `*.tld` (release-mgmt-account-bot@mozilla.tld) and `*.bugs`
# (Treeherder's intermittent-bug-filer@mozilla.bugs).
AUTOMATION_SUFFIXES = (".tld", ".bugs")
# A hung broker must not hold the job until the job timeout. The bug read fails the
# lookup at STATE_TIMEOUT; the history read only gives up at HISTORY_TIMEOUT.
STATE_TIMEOUT = 180
HISTORY_TIMEOUT = 120


def parse_time(value: object) -> dt.datetime | None:
    """A Bugzilla timestamp (`2026-10-13T18:01:21Z`) as an aware datetime."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            dt.timezone.utc
        )
    except ValueError:
        return None


STALE_AFTER = dt.timedelta(days=STALE_NEEDINFO_DAYS)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass(frozen=True)
class Needinfo:
    requestee: str
    setter: str
    since: dt.datetime | None

    def age_days(self, now: dt.datetime) -> float | None:
        if self.since is None:
            return None
        return (now - self.since).total_seconds() / 86400


def _lower(value: object) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


@dataclass
class TriageState:
    bug: dict
    comments: list[dict] = field(default_factory=list)
    now: dt.datetime = field(default_factory=_now)
    # Accounts that also count as the bot (the `BOT_ALIASES` env var).
    bot_aliases: frozenset[str] = BOT_ALIASES
    # The broker withheld the bug (`inaccessible`): `bug` holds only its id.
    restricted: bool = False
    # The broker's history of changes after the agent's newest comment, when the bug
    # changed after it: `when`, `who` and the changed `fields`. None when not read.
    history: tuple[dict, ...] | None = None

    @property
    def whiteboard(self) -> str:
        value = self.bug.get("whiteboard")
        return value if isinstance(value, str) else ""

    @property
    def reporter(self) -> str:
        return _lower(self.bug.get("creator"))

    @cached_property
    def people(self) -> frozenset[str]:
        """Everyone already on the bug: reporter, CC list and commenters, lowercased."""
        emails = {self.reporter}
        emails.update(_lower(e) for e in self.bug.get("cc") or ())
        emails.update(
            _lower(c.get("creator")) for c in self.comments if isinstance(c, dict)
        )
        emails.discard("")
        return frozenset(emails)

    @property
    def askable(self) -> frozenset[str]:
        """Who a needinfo may go to without a move: `people`, the experts, the fallback."""
        return self.people | NEEDINFO_EXPERTS | NEEDINFO_FALLBACK

    @cached_property
    def open_needinfos(self) -> tuple[Needinfo, ...]:
        """Every open needinfo on the bug, whoever set it."""
        found = []
        for flag in self.bug.get("flags") or ():
            if not isinstance(flag, dict):
                continue
            if flag.get("name") != "needinfo" or flag.get("status") != "?":
                continue
            found.append(
                Needinfo(
                    requestee=_lower(flag.get("requestee")),
                    setter=_lower(flag.get("setter")),
                    since=parse_time(flag.get("creation_date")),
                )
            )
        return tuple(found)

    @cached_property
    def bot_logins(self) -> frozenset[str]:
        """The bot account and its aliases, lowercased."""
        return frozenset({_lower(BOT_ACCOUNT), *map(_lower, self.bot_aliases)})

    @cached_property
    def oldest_needinfo(self) -> dt.datetime | None:
        """When the oldest open needinfo was set, whoever set it."""
        return min((n.since for n in self.open_needinfos if n.since), default=None)

    @cached_property
    def quiet_since(self) -> dt.datetime | None:
        """Since when the reporter has been quiet on the open needinfos.

        The latest of the newest open needinfo and the reporter's newest comment after
        the oldest one. A fresh ask restarts the clock, and so does a reply.
        """
        oldest = self.oldest_needinfo
        if oldest is None:
            return None
        newest = max(n.since for n in self.open_needinfos if n.since)
        replies = (
            parse_time(c.get("creation_time"))
            for c in self.comments
            if isinstance(c, dict) and _lower(c.get("creator")) == self.reporter
        )
        later = [t for t in replies if t is not None and t > oldest]
        return max([newest, *later])

    @property
    def reporter_is_developer_or_expert(self) -> bool:
        """Staff who are not QA, or a listed needinfo expert."""
        return (
            self.reporter in NEEDINFO_EXPERTS | NEEDINFO_FALLBACK
            or is_developer_reporter(self.bug)
        )

    @property
    def assignee(self) -> str | None:
        """The person the bug is assigned to, lowercased; None for the default nobody."""
        who = _lower(self.bug.get("assigned_to"))
        return None if who in ("", UNASSIGNED) else who

    @cached_property
    def newest_comment(self) -> dict | None:
        return next((c for c in reversed(self.comments) if isinstance(c, dict)), None)

    def _from_the_agent(self, comment: dict) -> bool:
        author = _lower(comment.get("creator"))
        if author == _lower(BOT_ACCOUNT):
            tags = comment.get("tags")
            return isinstance(tags, list) and AGENT_TAG in map(_lower, tags)
        return author in self.bot_logins

    @property
    def bot_has_last_word(self) -> bool:
        """Whether the agent wrote the newest comment and nothing changed the analysis after it.

        The agent's comment is one from `BOT_ACCOUNT` with the `AGENT_TAG` tag, since
        other Hackbot agents post as the same account. A `BOT_ALIASES` login counts by
        author alone, because the pilot bot may post without the tag. The bot's own
        update posts the comment and its field changes in one transaction, so a change
        at the comment's time is not after it. BMO counts `hackbot@mozilla.tld` as a
        person for `last_change_time_non_bot`, so any later change by a person moves
        that time past the comment. Then the broker's history decides: a change to a
        field that gives nothing new to read, such as CC, keeps the last word.
        """
        posted = self._agent_comment_time
        changed = self._change_time
        if posted is None or changed is None:
            return False
        return changed <= posted or self._nothing_to_read_since(posted)

    @cached_property
    def _agent_comment_time(self) -> dt.datetime | None:
        """When the agent wrote the newest comment, or None when it did not."""
        comment = self.newest_comment
        if comment is None or not self._from_the_agent(comment):
            return None
        return parse_time(comment.get("creation_time"))

    @property
    def changed_after_agent_comment(self) -> dt.datetime | None:
        """The agent's newest comment time, when the bug changed after it."""
        posted = self._agent_comment_time
        changed = self._change_time
        if posted is None or changed is None:
            return None
        return posted if changed > posted else None

    @property
    def _change_time(self) -> dt.datetime | None:
        return parse_time(self.bug.get("last_change_time_non_bot")) or parse_time(
            self.bug.get("last_change_time")
        )

    def _is_bot_login(self, who: object) -> bool:
        """An alias of the bot, or a BMO automation account other than `BOT_ACCOUNT`."""
        who = _lower(who)
        return who in self.bot_aliases_lower or (
            who.endswith(AUTOMATION_SUFFIXES) and who != _lower(BOT_ACCOUNT)
        )

    @cached_property
    def bot_aliases_lower(self) -> frozenset[str]:
        return frozenset(map(_lower, self.bot_aliases))

    def _nothing_to_read_since(self, posted: dt.datetime) -> bool:
        """Whether every change after `posted` gives the agent nothing new to read.

        A change counts when a person who is not a bot made it to a field that
        `TERMS.history` does not list, for example a comment-free status or keyword
        change. False when the history was not read, so the agent runs.
        """
        if self.history is None:
            return False
        for entry in self.history:
            when = parse_time(entry.get("when"))
            if when is None:
                return False
            if when <= posted or self._is_bot_login(entry.get("who")):
                continue
            fields = entry.get("fields") or ()
            if any(
                not isinstance(f, str) or TERMS.history.affects_analysis(f)
                for f in fields
            ):
                return False
        return True

    @cached_property
    def incomplete_allowed(self) -> bool:
        """Whether INCOMPLETE may be recorded.

        Every open needinfo on the bug is on the reporter, whoever set it, and there is
        at least one. Each has a date, and none is a reminder the reporter set on
        themselves. The reporter is not a developer and not a listed expert. The
        reporter has been quiet for `STALE_NEEDINFO_DAYS` (see `quiet_since`).
        """
        needinfos = self.open_needinfos
        if not needinfos:
            return False
        for n in needinfos:
            if n.requestee != self.reporter or n.setter == n.requestee or not n.since:
                return False
        if self.reporter_is_developer_or_expert:
            return False
        quiet = self.quiet_since
        return quiet is not None and self.now - quiet >= STALE_AFTER

    @property
    def closable_since_newest_comment(self) -> bool:
        """Whether INCOMPLETE became allowed after the newest comment.

        A bot that wrote the newest comment on a bug that was already closable had its
        chance to close it.
        """
        if not self.incomplete_allowed or self.quiet_since is None:
            return False
        comment = self.newest_comment
        posted = parse_time(comment.get("creation_time")) if comment else None
        return posted is None or self.quiet_since + STALE_AFTER > posted

    def describe(self) -> str:
        """A short state block for the prompt."""
        lines = []
        for n in self.open_needinfos:
            age = n.age_days(self.now)
            age_text = f"{age:.0f} days" if age is not None else "an unknown time"
            role = (
                "the reporter" if n.requestee == self.reporter else "not the reporter"
            )
            lines.append(
                f"An open needinfo is on {n.requestee} ({role}), set by "
                f"{n.setter or 'an unknown account'}, for {age_text}."
            )
        if not self.open_needinfos:
            lines.append("The bug has no open needinfo.")
        if self.incomplete_allowed:
            lines.append(
                "Closing as INCOMPLETE is permitted by the rules on this bug: every open "
                "needinfo is on the reporter, and the reporter has been quiet for "
                f"{STALE_NEEDINFO_DAYS} days."
            )
        else:
            lines.append(
                "Closing as INCOMPLETE is not permitted on this run; the other "
                "resolutions follow the rules as usual."
            )
        return "\n".join(lines)


async def fetch_state(
    bugzilla_mcp_server: McpServerConfig,
    bug: int,
    now: dt.datetime | None = None,
) -> TriageState | None:
    """The bug's fields and comments, read through the broker, as a :class:`TriageState`.

    Never raises. A bug the broker withholds gives a `restricted` state with no fields.
    None on a missing url, a broker failure, a read that takes more than
    `STATE_TIMEOUT` seconds, or a bug that is not returned for another reason; the
    caller decides what a missing state means for each consumer.
    """
    url = bugzilla_mcp_server.get("url")
    if not url:
        return None
    try:
        async with broker_session(url, STATE_TIMEOUT + HISTORY_TIMEOUT) as call:
            async with asyncio.timeout(STATE_TIMEOUT):
                bug_res = await call(
                    "get_bugs",
                    {
                        "ids": [bug],
                        "include_fields": BUG_FIELDS,
                        "include_comments": True,
                    },
                )
            bugs = bug_res.get("bugs") or []
            if not bugs and bug in (bug_res.get("inaccessible") or ()):
                return TriageState(bug={"id": bug}, now=now or _now(), restricted=True)
            if not bugs:
                return None
            fields = dict(bugs[0])
            comments = fields.pop("comments", None)
            if bug_res.get("comments_error") or not isinstance(comments, list):
                raise RuntimeError(
                    f"the comments were not read: {bug_res.get('comments_error')}"
                )
            state = TriageState(bug=fields, comments=comments, now=now or _now())
            since = state.changed_after_agent_comment
            if since is not None:
                state.history = await _history(call, bug, since)
    except Exception as e:  # see docstring
        print(
            f"[gecko_triage] state lookup for bug {bug} failed "
            f"({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return None
    return state


async def _history(call: Call, bug: int, since: dt.datetime) -> tuple[dict, ...] | None:
    """The bug's changes after `since`, or None when the broker fails.

    A failure only means that the agent runs, so it does not fail the state lookup.
    """
    try:
        async with asyncio.timeout(HISTORY_TIMEOUT):
            res = await call(
                "get_bug_history",
                {"bug_id": bug, "new_since": since.strftime("%Y-%m-%dT%H:%M:%SZ")},
            )
    except Exception as e:
        print(
            f"[gecko_triage] history lookup for bug {bug} failed "
            f"({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return None
    history = res.get("history")
    if not isinstance(history, list):
        return None
    return tuple(e for e in history if isinstance(e, dict))
