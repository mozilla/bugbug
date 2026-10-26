"""Tests for the state questions, answered from recorded Bugzilla responses."""

import asyncio
import datetime as dt

import pytest
from conftest import (
    BOT_COMMENTED,
    LATER,
    NOW,
    fake_broker,
    load,
    quiet,
    with_needinfo,
)
from hackbot_agents.gecko_triage import broker_client
from hackbot_agents.gecko_triage import state as state_module
from hackbot_agents.gecko_triage.config import AGENT_TAG, BOT_ACCOUNT, PEOPLE
from hackbot_agents.gecko_triage.state import (
    BUG_FIELDS,
    Needinfo,
    TriageState,
    fetch_state,
    parse_time,
)


def test_parse_time_reads_bugzilla_timestamps():
    assert parse_time("2026-10-13T12:34:56Z") == dt.datetime(
        2026, 10, 13, 12, 34, 56, tzinfo=dt.timezone.utc
    )
    assert parse_time(None) is None
    assert parse_time("") is None
    assert parse_time("yesterday") is None


def test_the_lookup_asks_for_what_the_state_reads():
    assert BUG_FIELDS.startswith("id,")
    for field in (
        "flags",
        "cc",
        "creator",
        "groups",
        "see_also",
        "last_change_time_non_bot",
        "cf_crash_signature",
    ):
        assert field in BUG_FIELDS.split(",")


def test_a_new_bug_waits_on_nobody_closable(new_state):
    assert not new_state.bot_has_last_word
    assert not new_state.incomplete_allowed


def test_people_are_the_reporter_the_cc_list_and_the_commenters(new_state):
    assert new_state.reporter == "reporter@example.com"
    assert new_state.people == {
        "reporter@example.com",
        "watcher@example.com",
        "dev@example.org",
        "component-bot@example.com",
    }


def test_askable_is_the_people_the_experts_and_the_fallback(new_state, monkeypatch):
    monkeypatch.setattr(state_module, "NEEDINFO_EXPERTS", frozenset({"e@example.org"}))
    monkeypatch.setattr(state_module, "NEEDINFO_FALLBACK", frozenset({"f@example.org"}))
    assert new_state.askable == new_state.people | {"e@example.org", "f@example.org"}


def test_open_needinfos_include_ones_set_by_others(new_state):
    # The developer's needinfo on the reporter is open, and it counts like any other.
    assert [n.requestee for n in new_state.open_needinfos] == ["reporter@example.com"]


def test_the_description_reads_no_earlier_comment(new_state, returning_state):
    # The returning fixture's tagged comment ends with an old footer line. The prompt
    # neither repeats it nor says the bug is returning: the model reads every comment.
    new, returning = new_state.describe(), returning_state.describe()
    for text in (new, returning):
        assert "first time" not in text
        assert "commented on this bug before" not in text
        assert "Last time it asked" not in text
        assert "about:support, media log" not in text
    assert new == returning


def test_the_bot_has_the_last_word_when_nothing_changed_after_its_comment(
    bug_new, comments_returning
):
    # The bot's PUT posts the comment and its field changes in one transaction, so
    # a change at the comment's time is not after it.
    s = TriageState(bug=quiet(bug_new), comments=comments_returning[:3], now=NOW)
    assert s.bot_has_last_word


def test_a_change_after_the_bot_comment_takes_the_last_word_away(
    bug_new, comments_returning
):
    # A person changed a field, with no comment, after the bot's comment.
    changed = quiet(bug_new, last_change_time_non_bot="2026-10-13T09:00:01Z")
    s = TriageState(bug=changed, comments=comments_returning[:3], now=NOW)
    assert not s.bot_has_last_word


PERSON = "dev@example.com"
AFTER = "2026-10-13T09:30:00Z"


def _changed_state(bug_new, comments_returning, history):
    bug = quiet(bug_new, last_change_time_non_bot=AFTER)
    return TriageState(
        bug=bug, comments=comments_returning[:3], now=NOW, history=history
    )


@pytest.mark.parametrize(
    "fields",
    [
        ["cc"],
        ["votes", "cf_rank"],
        ["cf_status_firefox150"],
        ["cf_tracking_firefox_esr140", "cc"],
    ],
)
def test_a_change_with_nothing_to_read_keeps_the_last_word(
    bug_new, comments_returning, fields
):
    history = ({"when": AFTER, "who": PERSON, "fields": fields},)
    assert _changed_state(bug_new, comments_returning, history).bot_has_last_word


@pytest.mark.parametrize(
    "fields",
    [["priority"], ["status"], ["keywords"], ["whiteboard"], ["cc", "severity"], [5]],
)
def test_a_change_the_agent_reads_takes_the_last_word_away(
    bug_new, comments_returning, fields
):
    history = ({"when": AFTER, "who": PERSON, "fields": fields},)
    assert not _changed_state(bug_new, comments_returning, history).bot_has_last_word


@pytest.mark.parametrize(
    "who",
    [
        "pilot-bot@example.com",
        "release-mgmt-account-bot@mozilla.tld",
        "intermittent-bug-filer@mozilla.bugs",
    ],
)
def test_a_bot_change_keeps_the_last_word(bug_new, comments_returning, who):
    history = ({"when": AFTER, "who": who, "fields": ["priority"]},)
    s = TriageState(
        bug=quiet(bug_new, last_change_time_non_bot=AFTER),
        comments=comments_returning[:3],
        now=NOW,
        history=history,
        bot_aliases=frozenset({"pilot-bot@example.com"}),
    )
    assert s.bot_has_last_word


def test_a_later_change_that_counts_takes_the_last_word_away(
    bug_new, comments_returning
):
    history = (
        {"when": AFTER, "who": PERSON, "fields": ["cc"]},
        {"when": "2026-10-13T09:40:00Z", "who": PERSON, "fields": ["priority"]},
    )
    assert not _changed_state(bug_new, comments_returning, history).bot_has_last_word


def test_an_empty_history_after_a_hidden_change_keeps_the_last_word(
    bug_new, comments_returning
):
    # The broker drops a change that a logged-out reader cannot see, such as a
    # private comment, so the agent has nothing new to read.
    assert _changed_state(bug_new, comments_returning, ()).bot_has_last_word


def test_a_change_by_the_hackbot_account_takes_the_last_word_away(
    bug_new, comments_returning
):
    # Other Hackbot agents post as the same account.
    history = ({"when": AFTER, "who": BOT_ACCOUNT, "fields": ["priority"]},)
    assert not _changed_state(bug_new, comments_returning, history).bot_has_last_word


def test_a_change_at_the_comment_time_is_part_of_the_bot_update(
    bug_new, comments_returning
):
    history = ({"when": BOT_COMMENTED, "who": PERSON, "fields": ["priority"]},)
    assert _changed_state(bug_new, comments_returning, history).bot_has_last_word


@pytest.mark.parametrize(
    "history", [None, ({"when": None, "who": PERSON, "fields": ["cc"]},)]
)
def test_an_unread_history_takes_the_last_word_away(
    bug_new, comments_returning, history
):
    assert not _changed_state(bug_new, comments_returning, history).bot_has_last_word


def test_the_history_is_asked_for_only_after_a_change(bug_new, comments_returning):
    assert (
        TriageState(
            bug=quiet(bug_new), comments=comments_returning[:3]
        ).changed_after_agent_comment
        is None
    )
    assert _changed_state(
        bug_new, comments_returning, None
    ).changed_after_agent_comment == dt.datetime(
        2026, 10, 13, 9, tzinfo=dt.timezone.utc
    )
    answered = quiet(bug_new, last_change_time_non_bot="2026-10-14T10:00:00Z")
    assert (
        TriageState(
            bug=answered, comments=comments_returning
        ).changed_after_agent_comment
        is None
    )


def test_a_person_comment_after_the_bot_takes_the_last_word_away(
    bug_new, comments_returning
):
    answered = quiet(bug_new, last_change_time_non_bot="2026-10-14T10:00:00Z")
    s = TriageState(bug=answered, comments=comments_returning, now=NOW)
    assert not s.bot_has_last_word


def test_the_plain_change_time_stands_in_for_the_bmo_field(bug_new, comments_returning):
    plain = dict(bug_new, last_change_time="2026-10-13T09:00:00Z")
    plain.pop("last_change_time_non_bot")
    assert TriageState(bug=plain, comments=comments_returning[:3]).bot_has_last_word
    plain["last_change_time"] = "2026-10-13T10:00:00Z"
    assert not TriageState(bug=plain, comments=comments_returning[:3]).bot_has_last_word


def test_a_hackbot_comment_without_the_tag_is_another_agents(
    bug_new, comments_returning
):
    # Other Hackbot agents post as the same account, without the gecko-triage tag.
    untagged = dict(comments_returning[2], tags=[], creator="HackBot@Mozilla.TLD")
    s = TriageState(
        bug=quiet(bug_new), comments=[*comments_returning[:2], untagged], now=NOW
    )
    assert not s.bot_has_last_word
    other = dict(untagged, tags=["bug-fix"])
    s = TriageState(
        bug=quiet(bug_new), comments=[*comments_returning[:2], other], now=NOW
    )
    assert not s.bot_has_last_word


def test_a_tagged_hackbot_comment_is_the_agents_last_word(bug_new, comments_returning):
    tagged = dict(comments_returning[2], creator="HackBot@Mozilla.TLD")
    assert AGENT_TAG in tagged["tags"]
    s = TriageState(
        bug=quiet(bug_new), comments=[*comments_returning[:2], tagged], now=NOW
    )
    assert s.bot_has_last_word

    # A tagged comment from a person does not count.
    person = dict(comments_returning[2], creator="dev@example.org")
    s = TriageState(
        bug=quiet(bug_new), comments=[*comments_returning[:2], person], now=NOW
    )
    assert not s.bot_has_last_word


def test_a_hackbot_comment_with_missing_or_odd_tags_is_not_the_agents(
    bug_new, comments_returning
):
    for comment in (
        {k: v for k, v in comments_returning[2].items() if k != "tags"},
        dict(comments_returning[2], tags=None),
        dict(comments_returning[2], tags="gecko-triage"),
        dict(comments_returning[2], tags=[None, 3]),
    ):
        s = TriageState(
            bug=quiet(bug_new), comments=[*comments_returning[:2], comment], now=NOW
        )
        assert not s.bot_has_last_word, comment.get("tags")


def test_the_tag_matches_in_any_case(bug_new, comments_returning):
    upper = dict(comments_returning[2], tags=["Gecko-Triage"])
    s = TriageState(
        bug=quiet(bug_new), comments=[*comments_returning[:2], upper], now=NOW
    )
    assert s.bot_has_last_word


def test_the_bot_account_needs_the_tag_even_as_an_alias(bug_new, comments_returning):
    untagged = dict(comments_returning[2], tags=[])
    s = TriageState(
        bug=quiet(bug_new),
        comments=[*comments_returning[:2], untagged],
        now=NOW,
        bot_aliases=frozenset({BOT_ACCOUNT}),
    )
    assert not s.bot_has_last_word


def test_the_tag_is_the_agent_name_hackbot_api_registers():
    import re
    from pathlib import Path

    agents = (
        Path(__file__).parents[3] / "services" / "hackbot-api" / "app" / "agents.py"
    )
    match = re.search(
        r'"gecko-triage":\s*AgentSpec\(\s*name="([^"]+)"', agents.read_text()
    )
    assert match and match[1] == AGENT_TAG


def test_an_alias_comment_gives_the_bot_the_last_word(bug_new, comments_returning):
    # The pilot bot may post without the tag, so an alias counts by author alone.
    for tags in ([], [AGENT_TAG]):
        alias = dict(comments_returning[2], tags=tags, creator="Triage-Bot@example.com")
        comments = [*comments_returning[:2], alias]
        assert TriageState(
            bug=quiet(bug_new),
            comments=comments,
            now=NOW,
            bot_aliases=frozenset({"triage-bot@example.com"}),
        ).bot_has_last_word, tags
        assert not TriageState(
            bug=quiet(bug_new), comments=comments, now=NOW
        ).bot_has_last_word, tags


def test_a_bug_with_unreadable_times_has_no_last_word(bug_new, comments_returning):
    bug = quiet(bug_new, last_change_time_non_bot=None, last_change_time=None)
    assert not TriageState(bug=bug, comments=comments_returning[:3]).bot_has_last_word
    timeless = dict(comments_returning[2], creation_time=None)
    assert not TriageState(bug=quiet(bug_new), comments=[timeless]).bot_has_last_word
    assert not TriageState(bug=quiet(bug_new), comments=[]).bot_has_last_word


STALE = BOT_COMMENTED
# 14 days after the bot's comment, to the second.
DAY_27 = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.timezone.utc)


def test_a_fresh_needinfo_does_not_permit_incomplete(bug_new, comments_returning):
    # The fixture's own needinfo, from the developer on the 13th, is fresh.
    s = TriageState(bug=bug_new, comments=comments_returning[:3], now=NOW)
    assert [n.requestee for n in s.open_needinfos] == ["reporter@example.com"]
    assert s.oldest_needinfo == parse_time("2026-10-13T12:00:00Z")
    assert not s.incomplete_allowed


def test_a_stale_needinfo_on_the_reporter_permits_incomplete(
    bug_new, comments_returning
):
    bug = with_needinfo(bug_new, "reporter@example.com", STALE)
    s = TriageState(bug=bug, comments=comments_returning[:3], now=LATER)
    assert Needinfo("reporter@example.com", BOT_ACCOUNT, parse_time(STALE)) in (
        s.open_needinfos
    )
    assert s.oldest_needinfo == parse_time(STALE)
    assert s.incomplete_allowed
    assert "INCOMPLETE is permitted" in s.describe()


def test_a_stale_needinfo_anyone_set_permits_incomplete(bug_new, comments_returning):
    # A person set it, not the bot: whoever asked, the reporter did not answer.
    for setter in ("dev@example.org", "Watcher@Example.com"):
        bug = with_needinfo(bug_new, "reporter@example.com", STALE, setter)
        s = TriageState(bug=bug, comments=comments_returning[:3], now=LATER)
        assert s.incomplete_allowed, setter


def test_a_reporter_reply_after_the_needinfo_blocks_incomplete(
    bug_new, comments_returning
):
    # The reporter answered on the 14th without clearing the flag. On the 27th the
    # needinfo is 14 days old, but the reporter has been quiet for 13 days only.
    bug = with_needinfo(bug_new, "reporter@example.com", STALE, "dev@example.org")
    s = TriageState(bug=bug, comments=comments_returning, now=DAY_27)
    assert s.quiet_since == parse_time("2026-10-14T10:00:00Z")
    assert not s.incomplete_allowed

    # Another person's comment after the needinfo is not an answer.
    other = dict(comments_returning[3], creator="watcher@example.com")
    s = TriageState(bug=bug, comments=[*comments_returning[:3], other], now=LATER)
    assert s.incomplete_allowed


def test_a_needinfo_on_anyone_else_blocks_incomplete(bug_new, comments_returning):
    # A needinfo on anyone else blocks closing, even alongside a stale one on the
    # reporter, and whoever set it.
    bug = with_needinfo(bug_new, "reporter@example.com", STALE)
    for setter in (BOT_ACCOUNT, "watcher@example.com"):
        both = with_needinfo(bug, "dev@example.org", "2026-10-14T09:00:00Z", setter)
        s = TriageState(bug=both, comments=comments_returning[:3], now=LATER)
        assert not s.incomplete_allowed, setter
        assert "INCOMPLETE is not permitted" in s.describe()
        # Only INCOMPLETE is ruled out: a duplicate or INVALID stays open to the model.
        assert "Closing this bug is not permitted" not in s.describe()

    only_dev = with_needinfo(dict(bug_new, flags=[]), "dev@example.org", STALE)
    assert not TriageState(
        bug=only_dev, comments=comments_returning[:3], now=LATER
    ).incomplete_allowed


def test_the_description_lists_every_open_needinfo(bug_new, comments_returning):
    bug = with_needinfo(
        bug_new, "dev@example.org", "2026-09-20T09:00:00Z", "watcher@example.com"
    )
    text = TriageState(bug=bug, comments=comments_returning[:3], now=NOW).describe()
    assert (
        "reporter@example.com (the reporter), set by dev@example.org, for 2 days"
        in text
    )
    assert "dev@example.org (not the reporter), set by watcher@example.com" in text
    no_flags = dict(bug_new, flags=[])
    assert "no open needinfo" in TriageState(bug=no_flags).describe()


async def test_a_broker_failure_yields_no_state(monkeypatch):
    assert await fetch_state({}, 1) is None
    assert await fetch_state({"type": "http"}, 1) is None

    def boom(url):
        raise ConnectionError("broker is down")

    monkeypatch.setattr(broker_client, "streamablehttp_client", boom)
    assert await fetch_state({"type": "http", "url": "http://broker/mcp"}, 1) is None


async def test_a_hung_broker_yields_no_state_after_the_timeout(monkeypatch):
    async def hang(args):
        await asyncio.sleep(10)

    fake_broker(monkeypatch, {"get_bugs": hang})
    monkeypatch.setattr(state_module, "STATE_TIMEOUT", 0.01)
    assert await fetch_state({"url": "http://broker/mcp"}, 9000001) is None


async def test_fetch_state_reads_the_bug_and_its_comments(monkeypatch):
    bug = load("bug_new.json")["bugs"][0]
    comments = load("comments_returning.json")["bugs"]["9000001"]["comments"]
    calls = fake_broker(
        monkeypatch,
        {
            "get_bugs": {
                "count": 1,
                "bugs": [dict(bug, comments=comments)],
                "inaccessible": [],
            },
        },
    )
    state = await fetch_state(
        {"type": "http", "url": "http://broker/mcp"},
        9000001,
        now=NOW,
    )
    assert calls[0] == ("connect", "http://broker/mcp")
    get_bugs = dict(calls)["get_bugs"]
    assert get_bugs == {
        "ids": [9000001],
        "include_fields": BUG_FIELDS,
        "include_comments": True,
    }
    # The broker strips `groups` unless asked, and the restricted skip reads it.
    assert "groups" in get_bugs["include_fields"].split(",")
    # One broker call reads the bug and its comments.
    assert [c[0] for c in calls] == ["connect", "get_bugs"]

    assert state.bug == bug and state.comments == comments
    assert state.now == NOW


async def test_fetch_state_reads_the_history_after_a_change(monkeypatch):
    bug = quiet(load("bug_new.json")["bugs"][0], last_change_time_non_bot=AFTER)
    comments = load("comments_returning.json")["bugs"]["9000001"]["comments"][:3]
    history = [{"when": AFTER, "who": PERSON, "fields": ["cc"]}]
    calls = fake_broker(
        monkeypatch,
        {
            "get_bugs": {
                "count": 1,
                "bugs": [dict(bug, comments=comments)],
                "inaccessible": [],
            },
            "get_bug_history": {"bug_id": 9000001, "history": history},
        },
    )
    state = await fetch_state({"url": "http://broker/mcp"}, 9000001, now=NOW)
    assert dict(calls)["get_bug_history"] == {
        "bug_id": 9000001,
        "new_since": BOT_COMMENTED,
    }
    assert state.history == tuple(history)
    assert state.bot_has_last_word


async def test_a_hung_history_read_still_gives_the_state(monkeypatch):
    bug = quiet(load("bug_new.json")["bugs"][0], last_change_time_non_bot=AFTER)
    comments = load("comments_returning.json")["bugs"]["9000001"]["comments"][:3]

    async def hang(args):
        await asyncio.sleep(10)

    fake_broker(
        monkeypatch,
        {
            "get_bugs": {
                "count": 1,
                "bugs": [dict(bug, comments=comments)],
                "inaccessible": [],
            },
            "get_bug_history": hang,
        },
    )
    monkeypatch.setattr(state_module, "HISTORY_TIMEOUT", 0.01)
    state = await fetch_state({"url": "http://broker/mcp"}, 9000001, now=NOW)
    assert state is not None and state.history is None
    assert not state.bot_has_last_word


async def test_a_history_failure_still_gives_the_state(monkeypatch):
    bug = quiet(load("bug_new.json")["bugs"][0], last_change_time_non_bot=AFTER)
    comments = load("comments_returning.json")["bugs"]["9000001"]["comments"][:3]
    fake_broker(
        monkeypatch,
        {
            "get_bugs": {
                "count": 1,
                "bugs": [dict(bug, comments=comments)],
                "inaccessible": [],
            },
            "get_bug_history": RuntimeError("broker down"),
        },
    )
    state = await fetch_state({"url": "http://broker/mcp"}, 9000001, now=NOW)
    assert state is not None and state.history is None
    assert not state.bot_has_last_word


async def test_fetch_state_marks_a_withheld_bug_as_restricted(monkeypatch):
    calls = fake_broker(
        monkeypatch,
        {"get_bugs": {"count": 0, "bugs": [], "inaccessible": [9000001]}},
    )
    state = await fetch_state({"url": "http://broker/mcp"}, 9000001, now=NOW)
    assert state.restricted
    assert state.bug == {"id": 9000001} and state.comments == []
    # The broker refuses the comments of a restricted bug, so none are asked for.
    assert [c[0] for c in calls] == ["connect", "get_bugs"]


async def test_fetch_state_yields_none_for_a_bug_it_cannot_read(monkeypatch):
    fake_broker(
        monkeypatch,
        {
            "get_bugs": RuntimeError("access_denied"),
        },
    )
    assert await fetch_state({"url": "http://broker/mcp"}, 9000001) is None

    fake_broker(
        monkeypatch,
        {
            "get_bugs": {"count": 0, "bugs": [], "inaccessible": []},
        },
    )
    assert await fetch_state({"url": "http://broker/mcp"}, 9000001) is None

    bug = load("bug_new.json")["bugs"][0]
    fake_broker(
        monkeypatch,
        {
            "get_bugs": {"count": 1, "bugs": [bug], "inaccessible": []},
        },
    )
    assert await fetch_state({"url": "http://broker/mcp"}, 9000001) is None


async def test_a_comments_error_fails_the_read(monkeypatch):
    bug = load("bug_new.json")["bugs"][0]
    error = {"code": None, "message": "bugzilla_error"}
    for reply in (
        {"count": 1, "bugs": [bug], "inaccessible": [], "comments_error": error},
        # An empty list with the error is not a bug with no comments.
        {
            "count": 1,
            "bugs": [dict(bug, comments=[])],
            "inaccessible": [],
            "comments_error": error,
        },
    ):
        fake_broker(monkeypatch, {"get_bugs": reply})
        assert await fetch_state({"url": "http://broker/mcp"}, 9000001) is None


def test_a_bug_closable_since_the_newest_comment(bug_new, comments_returning):
    # The needinfo went stale on the 27th, after the bot's comment of the 13th.
    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com")
    s = TriageState(bug=bug, comments=comments_returning[:3], now=LATER)
    assert s.closable_since_newest_comment

    # The bot wrote again on 1 November, after the bug became closable.
    again = dict(comments_returning[2], creation_time="2026-11-01T09:00:00Z")
    s = TriageState(bug=bug, comments=[*comments_returning[:3], again], now=LATER)
    assert s.incomplete_allowed and not s.closable_since_newest_comment

    # Not closable at all.
    assert not TriageState(
        bug=bug, comments=comments_returning[:3], now=NOW
    ).closable_since_newest_comment


def test_a_bot_comment_exactly_when_the_bug_became_closable_is_not_after_it(
    bug_new, comments_returning
):
    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com")
    again = dict(comments_returning[2], creation_time="2026-10-27T09:00:00Z")
    s = TriageState(bug=bug, comments=[*comments_returning[:3], again], now=LATER)
    assert s.quiet_since + dt.timedelta(days=14) == parse_time(again["creation_time"])
    assert s.incomplete_allowed and not s.closable_since_newest_comment


def test_fourteen_quiet_days_after_a_reporter_reply_permit_incomplete(
    bug_new, comments_returning
):
    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com")
    replied = dt.datetime(2026, 10, 28, 10, 0, tzinfo=dt.timezone.utc)
    s = TriageState(bug=bug, comments=comments_returning, now=replied)
    assert s.incomplete_allowed
    assert not TriageState(
        bug=bug, comments=comments_returning, now=replied - dt.timedelta(seconds=1)
    ).incomplete_allowed


def test_a_reporter_comment_in_the_same_second_as_the_needinfo_is_not_a_reply(
    bug_new, comments_returning
):
    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com")
    same = dict(comments_returning[3], creation_time=BOT_COMMENTED)
    s = TriageState(bug=bug, comments=[*comments_returning[:3], same], now=DAY_27)
    assert s.quiet_since == parse_time(BOT_COMMENTED)
    assert s.incomplete_allowed


def test_a_self_set_reminder_on_the_reporter_blocks_incomplete(
    bug_new, comments_returning
):
    # Bug 1427933: the reporter set a needinfo on themselves as a reminder.
    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com")
    bug = with_needinfo(bug, "reporter@example.com", setter="Reporter@example.com")
    assert not TriageState(
        bug=bug, comments=comments_returning[:3], now=LATER
    ).incomplete_allowed


def test_a_developer_or_expert_reporter_blocks_incomplete(
    bug_new, comments_returning, monkeypatch
):
    staff = f"dev{PEOPLE.employee_domain}"
    bug = with_needinfo(dict(bug_new, flags=[], creator=staff), staff)
    bug["creator_detail"] = dict(bug_new["creator_detail"], real_name="Dev Eloper")
    assert not TriageState(
        bug=bug, comments=comments_returning[:3], now=LATER
    ).incomplete_allowed

    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com")
    s = TriageState(bug=bug, comments=comments_returning[:3], now=LATER)
    assert s.incomplete_allowed
    monkeypatch.setattr(
        state_module, "NEEDINFO_EXPERTS", frozenset({"reporter@example.com"})
    )
    assert not TriageState(
        bug=bug, comments=comments_returning[:3], now=LATER
    ).incomplete_allowed


def test_a_needinfo_without_a_date_blocks_incomplete(bug_new, comments_returning):
    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com")
    undated = dict(bug["flags"][0])
    undated.pop("creation_date")
    bug["flags"].append(undated)
    assert not TriageState(
        bug=bug, comments=comments_returning[:3], now=LATER
    ).incomplete_allowed


def test_a_non_bot_time_later_than_the_change_time_wins(bug_new, comments_returning):
    # The BMO field is read first, so a later non-bot time means a later change.
    bug = quiet(
        bug_new,
        last_change_time=BOT_COMMENTED,
        last_change_time_non_bot="2026-10-13T10:00:00Z",
    )
    assert not TriageState(bug=bug, comments=comments_returning[:3]).bot_has_last_word


def test_the_description_names_an_unknown_setter(bug_new):
    flag = {"name": "needinfo", "status": "?", "requestee": "reporter@example.com"}
    text = TriageState(bug=dict(bug_new, flags=[flag]), now=NOW).describe()
    assert (
        "An open needinfo is on reporter@example.com (the reporter), set by an "
        "unknown account, for an unknown time." in text
    )


def test_a_newer_reporter_needinfo_restarts_the_clock(bug_new, comments_returning):
    # With `new: true` one person can hold two needinfos. A fresh ask is not closed.
    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com")
    bug = with_needinfo(bug, "reporter@example.com", "2026-11-10T09:00:00Z")
    s = TriageState(bug=bug, comments=comments_returning[:3], now=LATER)
    assert s.oldest_needinfo == parse_time(BOT_COMMENTED)
    assert s.quiet_since == parse_time("2026-11-10T09:00:00Z")
    assert not s.incomplete_allowed


def test_a_reply_moves_the_closable_time_past_a_later_bot_comment(
    bug_new, comments_returning
):
    # The reporter replied on the 14th, so the bug became closable on the 28th. The
    # bot's comment of the 27th, at noon, came before that.
    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com")
    bot = dict(comments_returning[2], creation_time="2026-10-27T12:00:00Z")
    after_28th = dt.datetime(2026, 10, 29, 9, 0, tzinfo=dt.timezone.utc)
    s = TriageState(bug=bug, comments=[*comments_returning, bot], now=after_28th)
    assert s.quiet_since != s.oldest_needinfo
    assert s.incomplete_allowed and s.closable_since_newest_comment


def test_a_qa_reporter_in_the_staff_domain_may_be_closed(bug_new, comments_returning):
    staff = f"qa{PEOPLE.employee_domain}"
    bug = with_needinfo(dict(bug_new, flags=[], creator=staff), staff)
    bug["creator_detail"] = dict(bug_new["creator_detail"], real_name="Tess Ter, QA")
    s = TriageState(bug=bug, comments=comments_returning[:3], now=LATER)
    assert not s.reporter_is_developer_or_expert
    assert s.incomplete_allowed


def test_the_state_and_the_preflight_import_each_other_only_for_types():
    from hackbot_agents.gecko_triage import preflight

    assert not hasattr(preflight, "TriageState")
