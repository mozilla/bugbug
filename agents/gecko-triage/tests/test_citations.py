"""Tests for the comment numbers that a triage comment cites."""

import asyncio
import json

import pytest
from hackbot_agents.gecko_triage.citations import (
    COMMENTS_TOOL,
    ReadComments,
    citable_text,
    citation_refusal,
    note_comments,
    note_read,
    read_hook,
)
from hackbot_agents.gecko_triage.withheld import GET_BUGS_TOOL

BUG = 9000001
OTHER = 9000002


def _comment(count, attachment_id=None, text="x"):
    return {"count": count, "attachment_id": attachment_id, "text": text}


def _read(*comments, bug=OTHER):
    store = ReadComments()
    note_comments(store, BUG, [_comment(0), _comment(1, 555)])
    note_comments(store, bug, list(comments))
    return store


def _blocks(payload):
    return [{"type": "text", "text": json.dumps(payload)}]


def test_a_get_bug_comments_result_is_noted():
    store = ReadComments()
    payload = {"bug_id": OTHER, "comments": [_comment(3, 777), _comment(4)]}
    note_read(store, {"tool_name": COMMENTS_TOOL, "tool_response": _blocks(payload)})
    assert store.seen == {(OTHER, 3): True, (OTHER, 4): False}


def test_the_comments_of_a_get_bugs_result_are_noted():
    store = ReadComments()
    payload = {"bugs": [{"id": OTHER, "comments": [_comment(2)]}], "inaccessible": []}
    note_read(store, {"tool_name": GET_BUGS_TOOL, "tool_response": _blocks(payload)})
    assert store.seen == {(OTHER, 2): False}


def test_another_tool_and_a_bad_result_note_nothing():
    store = ReadComments()
    note_read(store, {"tool_name": "mcp__searchfox__get_file", "tool_response": "x"})
    note_read(store, {"tool_name": COMMENTS_TOOL, "tool_response": "not json"})
    bad = {"bug_id": "9000002", "comments": [{"count": "3"}, "x"]}
    note_read(store, {"tool_name": COMMENTS_TOOL, "tool_response": _blocks(bad)})
    assert store.seen == {}


def test_the_read_hook_notes_the_result():
    store = ReadComments()
    payload = {"bug_id": OTHER, "comments": [_comment(1)]}
    hook = read_hook(store)
    result = asyncio.run(
        hook(
            {"tool_name": COMMENTS_TOOL, "tool_response": _blocks(payload)}, None, None
        )
    )
    assert result == {} and (OTHER, 1) in store.seen


def test_a_cited_comment_of_another_bug_must_have_been_read():
    store = _read(_comment(3))
    reason = citation_refusal(f"See bug {OTHER} comment 5.", BUG, store)
    assert f"comment 5 of bug {OTHER}" in reason and "get_bug_comments" in reason
    assert citation_refusal(f"See bug {OTHER} comment 3.", BUG, store) is None


@pytest.mark.parametrize(
    "text",
    [
        f"Your WIP patch on bug {OTHER} (comment 4) already has some of these checks.",
        f"The patch in bug {OTHER}, comment 4, has some of them.",
        f"The attachment in bug {OTHER} comment 4 shows it.",
    ],
)
def test_a_patch_is_cited_by_the_comment_that_adds_it(text):
    store = _read(_comment(3, 777), _comment(4))
    reason = citation_refusal(text, BUG, store)
    assert "comment 4" in reason and "adds no attachment" in reason
    assert citation_refusal(text.replace("comment 4", "comment 3"), BUG, store) is None


def test_a_comment_cited_for_something_else_needs_no_attachment():
    store = _read(_comment(4))
    text = f"The reporter tested it in bug {OTHER} (comment 4)."
    assert citation_refusal(text, BUG, store) is None


def test_a_comment_of_the_triaged_bug_is_checked_too():
    store = _read()
    assert citation_refusal("As comment 1 shows, the patch works.", BUG, store) is None
    reason = citation_refusal("The patch in comment 0 fixes it.", BUG, store)
    assert "comment 0" in reason and "adds no attachment" in reason
    assert "comment 9" in citation_refusal("See comment 9.", BUG, store)


def test_a_comment_with_no_citation_passes():
    assert citation_refusal("The patch fixes it.", BUG, ReadComments()) is None


def _spill(path):
    return (
        "Error: result (912,334 characters) exceeds maximum allowed tokens. Output has "
        f"been saved to {path}.\n"
    )


def test_a_result_saved_to_a_file_is_noted(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    folder = tmp_path / "config" / "projects" / "-r" / "s" / "tool-results"
    folder.mkdir(parents=True)
    path = folder / "mcp-bugzilla-get_bug_comments-1.txt"
    payload = {"bug_id": OTHER, "comments": [_comment(3, 777)]}
    path.write_text(json.dumps(_blocks(payload)))
    store = ReadComments()
    note_read(store, {"tool_name": COMMENTS_TOOL, "tool_response": _spill(path)})
    assert store.seen == {(OTHER, 3): True}


def test_a_comment_that_lands_a_change_may_be_cited_for_the_patch():
    pushed = "Pushed by dev@example.com:\nhttps://hg.mozilla.org/integration/autoland/rev/abc"
    merged = "https://github.com/mozilla-firefox/firefox/commit/abc"
    store = _read(_comment(12, text=pushed), _comment(13, text=merged))
    assert store.seen[(OTHER, 12)] and store.seen[(OTHER, 13)]
    text = f"The patch landed in bug {OTHER} (comment 12)."
    assert citation_refusal(text, BUG, store) is None


@pytest.mark.parametrize(
    "text",
    [
        f"The WIP patch is in comment 4 of bug {OTHER}.",
        f"The WIP patch in bug {OTHER}'s comment 4 has them.",
        f"The WIP patch in bug {OTHER} comment #4 has them.",
        f"The WIP patch (bug {OTHER}#c4) has them.",
    ],
)
def test_every_form_of_a_cited_comment_of_another_bug_is_checked(text):
    store = _read(_comment(3, 777), _comment(4))
    reason = citation_refusal(text, BUG, store)
    assert f"comment 4 of bug {OTHER}" in reason and "adds no attachment" in reason


def test_a_patch_is_checked_against_the_nearest_citation_only():
    store = _read(_comment(3, 777), _comment(5))
    text = (
        f"The patch in bug {OTHER} comment 3 fixes what bug {OTHER} comment 5 reports."
    )
    assert citation_refusal(text, BUG, store) is None
    assert (
        citation_refusal("The attachment in comment 1 shows comment 0.", BUG, store)
        is None
    )


def test_a_word_that_only_starts_with_patch_is_no_patch():
    store = _read(_comment(5))
    text = f"The reporter patched it locally (bug {OTHER} comment 5)."
    assert citation_refusal(text, BUG, store) is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            f"the [WIP patch](https://bugzilla.mozilla.org/show_bug.cgi?id={OTHER}#c4)",
            f"the WIP patch (bug {OTHER} comment 4)",
        ),
        (
            f"on [bug {OTHER}](https://bugzilla.mozilla.org/show_bug.cgi?id={OTHER}) (comment 4)",
            f"on bug {OTHER} (comment 4)",
        ),
        (
            f"see https://bugzilla.mozilla.org/show_bug.cgi?id={OTHER}#c4 now",
            f"see bug {OTHER} comment 4 now",
        ),
    ],
)
def test_a_citation_in_a_link_is_kept_for_the_check(body, expected):
    assert citable_text(body) == expected


def test_a_citation_before_the_patch_word_is_not_checked_for_an_attachment():
    store = _read(_comment(5))
    text = f"The reporter confirmed in bug {OTHER} comment 5 that the patch fixes it."
    assert citation_refusal(text, BUG, store) is None
