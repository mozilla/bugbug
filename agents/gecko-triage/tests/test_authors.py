"""Tests for the commit authors that the `mozilla_vcs` tool results name."""

import json
import re

import pytest
from agent_tools.mozilla_vcs import MozillaVcsContext
from hackbot_agents.gecko_triage import authors
from hackbot_agents.gecko_triage.config import MOZILLA_VCS_TOOLS

INFO = {
    "node": "abc",
    "author": "Jane Dev <Jane@Example.org>",
    "desc": "Bug 9000003 - Fix it. Patch by Other <other@example.org> r=rev",
}
HISTORY = {
    "changesets": [
        {"node": "a", "author": "A <a@example.org>", "desc": "x"},
        {"node": "b", "author": "no address", "desc": "y"},
        {"node": "c", "author": "C <c@example.com>", "desc": "<d@example.com>"},
    ]
}


def _content(data):
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_content(INFO), {"jane@example.org"}),
        (_content(HISTORY), {"a@example.org", "c@example.com"}),
        (_content({"user": "U <u@example.org>"}), set()),
        (_content({"pushlog": {"author": "P <p@example.org>"}}), set()),
        (
            _content({"author": "A <a@example.org>, B <b@example.org>"}),
            {"a@example.org"},
        ),
        ("not json <x@example.org>", set()),
        (None, set()),
    ],
)
def test_the_author_emails_come_from_the_author_fields_only(response, expected):
    assert authors.commit_author_emails(response) == expected


async def test_the_hook_adds_to_the_runs_facts():
    found = authors.CommitFacts()
    hook = authors.commit_hook(found)
    assert await hook({"tool_response": _content(HISTORY)}, "toolu_1", None) == {}
    assert found.authors == {"a@example.org", "c@example.com"}
    await hook({"tool_response": _content(INFO)}, "toolu_2", None)
    assert found.bugs == {9000003}


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        ("Bug 1234567 - Fix it. r=me", {1234567}),
        ("Backed out changeset abc (bug 1234567) for failures", {1234567}),
        ("Bug 1234567, bug 7654321 - Two", {1234567, 7654321}),
        ("No bug here, 1234567 alone", set()),
        ("Bugs 1234567 and 7654321 - Two", {1234567, 7654321}),
        ("Bugs 1234567, 7654321, and 1111111", {1234567, 7654321, 1111111}),
        ("Bug 1234567 & 7654321", {1234567, 7654321}),
        ("Fix the leak. b=1234567 r=me", {1234567}),
        ("Fix bmo#1234567 and bug 7654321", {1234567, 7654321}),
        ("Bug 99999 and bug 10000000 - out of range", set()),
        ("Bug 100000 and bug 9999999 - edges", {100000, 9999999}),
        (
            "Fix it, see https://bugzilla.mozilla.org/show_bug.cgi?id=1234567",
            {1234567},
        ),
        (
            "See https://bugzilla.mozilla.org/1234567 and bugzil.la/7654321",
            {1234567, 7654321},
        ),
    ],
)
def test_the_commit_message_bugs_come_from_desc(desc, expected):
    response = _content({"changesets": [{"author": "A <a@x.org>", "desc": desc}]})
    assert authors.commit_bug_ids(response) == expected


DIFF = """# HG changeset patch
# User Jane Dev <jane@example.org>
# Date 1729353600 -25200
# Node ID 6b8a3f8047890000000000000000000000000000
Bug 1234567 - Fix the parser. r=rev

Also covers bug 7654321.

diff --git a/dom/media/Foo.cpp b/dom/media/Foo.cpp
+  // see bug 1111111
diff --git a/dom/media/test/crashtests/2222222.html b/dom/media/test/crashtests/2222222.html
+  <video src="3333333.webm">
"""


def test_a_diff_gives_its_message_bugs_and_the_explicit_ones_of_its_file_diffs():
    response = _content({"node": "6b8a3f804789", "truncated": False, "diff": DIFF})
    assert authors.commit_bug_ids(response) == {1234567, 7654321, 1111111}
    assert authors.commit_author_emails(response) == set()


def test_a_missing_repo_is_the_tools_default_mozilla_central():
    assert MozillaVcsContext().default_repo == "mozilla-central"
    assert authors.DEFAULT_REPO == "mozilla-central"


@pytest.mark.parametrize(
    ("tool_input", "main"),
    [
        ({}, True),
        ({"repo": None}, True),
        ({"repo": "mozilla-central"}, True),
        ({"repo": "autoland"}, True),
        ({"repo": "integration/autoland"}, True),
        ({"repo": "mozilla-beta"}, True),
        ({"repo": "releases/mozilla-release"}, True),
        ({"repo": "releases/mozilla-esr140"}, True),
        ({"repo": "try"}, False),
        ({"repo": "mozilla-unified"}, False),
        ({"repo": "users/someone/repo"}, False),
    ],
)
def test_only_a_main_repo_counts(tool_input, main):
    assert authors.is_main_repo(tool_input) is main


async def test_the_hook_ignores_a_try_repo():
    found = authors.CommitFacts()
    hook = authors.commit_hook(found)
    await hook(
        {"tool_input": {"repo": "try"}, "tool_response": _content(INFO)},
        "toolu_1",
        None,
    )
    assert found == authors.CommitFacts()
    await hook(
        {"tool_input": {"node": "abc"}, "tool_response": _content(INFO)},
        "toolu_1",
        None,
    )
    assert found.authors == found.commit_emails == {"jane@example.org"}
    assert found.bug_authors == {9000003: {"jane dev"}}


def test_the_matcher_covers_the_vcs_tools_that_show_a_commit():
    matcher = re.compile(authors.COMMIT_TOOLS_MATCHER)
    assert set(authors.COMMIT_TOOLS) <= set(MOZILLA_VCS_TOOLS)
    assert matcher.fullmatch("mcp__mozilla_vcs__get_commit_diff")
    assert all(matcher.fullmatch(t) for t in authors.COMMIT_TOOLS)
    assert not matcher.fullmatch("mcp__bugzilla__get_bugs")


COMMIT_BUG = 9000003
ASSIGNEE = "dev@mozilla.com"
JANE = {"email": ASSIGNEE, "real_name": "Jane  DEV [:jane] (away)"}


def _get_bugs(*bugs, inaccessible=()):
    return {
        "tool_name": authors.GET_BUGS_TOOL,
        "tool_input": {},
        "tool_response": _content(
            {"bugs": list(bugs), "inaccessible": list(inaccessible)}
        ),
    }


def _bug(email=ASSIGNEE, name="Jane Dev", bug=COMMIT_BUG):
    return {
        "id": bug,
        "assigned_to": email,
        "assigned_to_detail": {"email": email, "real_name": name},
    }


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Jane  DEV [:jane] (away)", "jane dev"),
        (" Chun-Min Chang [:chunmin] ", "chun-min chang"),
        ("Jos\u00e9 N\u00fa\u00f1ez", "jose nunez"),
        ("Jose\u0301 Nu\u0301n\u0303ez", "jose nunez"),
        ('"Jane Dev" ', "jane dev"),
        ("\u201cJane Dev\u201d", "jane dev"),
        ("Jane Dev [:jane", "jane dev"),
        ("Jane Dev, :jane", "jane dev"),
        ("Jane Dev, :jane (away)", "jane dev"),
        (None, ""),
    ],
)
def test_a_name_loses_its_nick_notes_quotes_accents_and_case(name, expected):
    assert authors.normalized_name(name) == expected


def test_each_commit_bug_maps_to_its_commits_author_names():
    history = {
        "changesets": [
            {"author": "Jane Dev <j@example.org>", "desc": "Bug 9000003 - Fix"},
            {"author": "Sheriff <s@example.org>", "desc": "Backed out (bug 9000003)"},
            {"author": "<n@example.org>", "desc": "Bug 9000004 - Other"},
        ]
    }
    assert authors.commit_bug_authors(_content(history)) == {
        COMMIT_BUG: {"jane dev", "sheriff"},
        9000004: set(),
    }


@pytest.mark.parametrize(
    ("bug", "expected"),
    [
        (
            {
                "id": COMMIT_BUG,
                "assigned_to": " Dev@Mozilla.com ",
                "assigned_to_detail": JANE,
            },
            {COMMIT_BUG: (ASSIGNEE, "jane dev")},
        ),
        (
            {"id": COMMIT_BUG, "assigned_to_detail": JANE},
            {COMMIT_BUG: (ASSIGNEE, "jane dev")},
        ),
        ({"id": COMMIT_BUG, "assigned_to": ASSIGNEE}, {COMMIT_BUG: (ASSIGNEE, "")}),
        ({"id": COMMIT_BUG, "assigned_to": "nobody@mozilla.org"}, {}),
        ({"id": COMMIT_BUG, "assigned_to": "dev@example.org"}, {}),
        ({"id": COMMIT_BUG, "assigned_to": "dev@mozilla.com.example.org"}, {}),
        ({"id": COMMIT_BUG}, {}),
        ({"id": "9000003", "assigned_to": ASSIGNEE}, {}),
        ({"assigned_to": ASSIGNEE}, {}),
    ],
    ids=[
        "both",
        "detail-only",
        "no-name",
        "nobody",
        "not-mozilla",
        "lookalike",
        "unassigned",
        "string-id",
        "no-id",
    ],
)
def test_only_a_mozilla_com_assignee_counts(bug, expected):
    assert authors.mozilla_assignees(_get_bugs(bug)["tool_response"]) == expected


def _facts(name="Jane Dev"):
    found = authors.CommitFacts()
    found.add(bug_authors={COMMIT_BUG: {authors.normalized_name(name)}})
    return found


async def test_the_hook_adds_the_assignee_who_wrote_the_commit():
    found = _facts()
    hook = authors.assignee_hook(found)
    other = _bug("other@mozilla.com", "Jane Dev", 9000004)
    assert await hook(_get_bugs(_bug(name="jane dev [:jd]"), other), "t", None) == {}
    assert found.authors == found.assignee_authors == {ASSIGNEE}


async def test_a_detail_only_result_gives_the_assignee():
    found = _facts()
    detail_only = {"id": COMMIT_BUG, "assigned_to_detail": JANE}
    await authors.assignee_hook(found)(_get_bugs(detail_only), "t", None)
    assert found.authors == {ASSIGNEE}


@pytest.mark.parametrize(
    "bug",
    [
        _bug(name="Someone Else"),
        {"id": COMMIT_BUG, "assigned_to": ASSIGNEE},
        _bug("nobody@mozilla.org"),
        _bug("dev@example.org"),
    ],
    ids=["other-name", "no-name", "nobody", "not-mozilla"],
)
async def test_the_hook_ignores_an_assignee_it_cannot_tie_to_the_commit(bug):
    found = _facts()
    await authors.assignee_hook(found)(_get_bugs(bug), "t", None)
    assert found.authors == found.assignee_authors == set()


async def test_a_backed_out_bug_does_not_make_its_assignee_askable():
    found = authors.CommitFacts()
    backout = {"author": "Sheriff <s@example.org>", "desc": "Backed out (bug 9000003)"}
    await authors.commit_hook(found)({"tool_response": _content(backout)}, "t", None)
    await authors.assignee_hook(found)(_get_bugs(_bug()), "t", None)
    assert found.bugs == {COMMIT_BUG}
    assert found.authors == {"s@example.org"}


async def test_a_restricted_commit_bug_adds_nothing():
    found = _facts()
    await authors.assignee_hook(found)(
        _get_bugs(inaccessible=[COMMIT_BUG]), "toolu_1", None
    )
    assert found.authors == set()


async def test_an_assignee_read_before_the_commit_counts_once_the_commit_names_it():
    found = authors.CommitFacts()
    await authors.assignee_hook(found)(_get_bugs(_bug()), "toolu_1", None)
    assert found.authors == set()
    await authors.commit_hook(found)({"tool_response": _content(INFO)}, "t", None)
    assert found.authors == {"jane@example.org", ASSIGNEE}


async def test_the_assignee_hook_skips_other_broker_tools():
    found = _facts()
    data = _get_bugs(_bug())
    data["tool_name"] = "mcp__bugzilla__search_bugs"
    await authors.assignee_hook(found)(data, "toolu_1", None)
    assert found.authors == set()


async def test_a_quoted_accented_hg_name_matches_the_bugzilla_name():
    found = authors.CommitFacts()
    commit = {"author": '"Jos\u00e9 Dev" <j@example.org>', "desc": "Bug 9000003 - Fix"}
    await authors.commit_hook(found)({"tool_response": _content(commit)}, "t", None)
    bug = _bug(name="Jose\u0301 Dev [:jd")
    await authors.assignee_hook(found)(_get_bugs(bug), "t", None)
    assert found.assignee_authors == {ASSIGNEE}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Jane:Smith", "jane:smith"),
        ("Jane Dev, :jdev, :jd", "jane dev"),
        ("Jane Dev :jane", "jane dev"),
    ],
)
def test_a_nick_needs_a_comma_or_a_space_before_its_colon(raw, expected):
    assert authors.normalized_name(raw) == expected
