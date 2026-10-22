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
    assert found == authors.CommitFacts({"jane@example.org"}, {9000003})


def test_the_matcher_covers_the_vcs_tools_that_show_a_commit():
    matcher = re.compile(authors.COMMIT_TOOLS_MATCHER)
    assert set(authors.COMMIT_TOOLS) <= set(MOZILLA_VCS_TOOLS)
    assert matcher.fullmatch("mcp__mozilla_vcs__get_commit_diff")
    assert all(matcher.fullmatch(t) for t in authors.COMMIT_TOOLS)
    assert not matcher.fullmatch("mcp__bugzilla__get_bugs")
