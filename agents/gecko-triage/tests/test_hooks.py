"""Tests for the record-time limits on what this agent may write to a bug.

Every refusal, and that a refusal does not consume the allowance. The hooks run inside
the agent as the action is recorded, so a rejection reaches the model as a tool error
and the action never lands in summary.json.
"""

import datetime as dt

import pytest
from agent_tools.registry import ToolError
from conftest import (
    BUG,
    DUPE_TARGET,
    KNOWN_META,
    LATER,
    NOW,
    REACTION_FOOTER,
    _comment,
    _dupe_recorder,
    _duplicate_of_target,
    _recorder,
    _text,
    _update,
    _verdicts,
    with_needinfo,
)
from hackbot_agents.gecko_triage import hooks
from hackbot_agents.gecko_triage import state as state_module
from hackbot_agents.gecko_triage.closing import CloseVerdict, CloseVerdicts
from hackbot_agents.gecko_triage.config import (
    BOT_ACCOUNT,
    CAPTURE_PLACEHOLDER,
    ENABLED_ACTION_TYPES,
    MOZREGRESSION_BLOCK,
    MOZREGRESSION_PLACEHOLDER,
    TEAM,
    TEAM_META_BUGS,
    TERMS,
    TRIAGE_PRIORITIES,
)
from hackbot_agents.gecko_triage.investigations import Investigations, note_spawn
from hackbot_agents.gecko_triage.state import TriageState
from hackbot_runtime.actions.claude_sdk import actions_to_tool_names
from hackbot_runtime.searchfox import PLACEHOLDER as SEARCHFOX_PLACEHOLDER


def test_the_agent_is_given_a_comment_and_a_field_change_and_nothing_else():
    assert ENABLED_ACTION_TYPES == ["bugzilla.add_comment", "bugzilla.update_bug"]
    tools = actions_to_tool_names(ENABLED_ACTION_TYPES)
    assert not [t for t in tools if "create_bug" in t or "attachment" in t], tools


# --- the comment ------------------------------------------------------------ #


def test_a_private_comment_is_refused():
    rec = _recorder()
    with pytest.raises(ToolError):
        _comment(rec, is_private=True)
    assert rec.actions == []


def test_a_comment_against_another_bug_is_refused():
    for bug_id in (999, "9000001", None):
        rec = _recorder()
        with pytest.raises(ToolError):
            _comment(rec, bug_id=bug_id)
        assert rec.actions == []


def test_only_one_comment_may_be_recorded():
    rec = _recorder()
    _comment(rec)
    with pytest.raises(ToolError):
        _comment(rec)
    assert len(rec.actions) == 1


def test_a_refused_comment_does_not_use_up_the_allowance():
    rec = _recorder()
    with pytest.raises(ToolError):
        _comment(rec, is_private=True)
    with pytest.raises(ToolError):
        _comment(rec, bug_id=999)
    _comment(rec)
    assert len(rec.actions) == 1


def test_a_comment_without_the_runtime_trailer_is_left_alone():
    rec = _recorder()
    assert _comment(rec, text="Short note.")["params"]["text"] == "Short note."


def test_the_style_limits_skip_the_runtime_trailer():
    # The trailer has emoji and is not the agent's prose, so it is kept as it is.
    rec = _recorder()
    text = _comment(rec)["params"]["text"]
    assert text == f"Short note.{REACTION_FOOTER}"
    assert hooks.split_runtime_footer(text) == ("Short note.", REACTION_FOOTER)


def test_the_full_hook_chain_rewrites_the_comment_in_order(tmp_path):
    (tmp_path / "dom" / "media").mkdir(parents=True)
    (tmp_path / "dom" / "media" / "MediaDecoder.cpp").write_text("")
    rec = _recorder(searchfox_rev="abc123", repo=tmp_path)
    body = (
        f"The decoder stops in [MediaDecoder.cpp]({SEARCHFOX_PLACEHOLDER}"
        f"/dom/media/MediaDecoder.cpp#42), not in [Gone.cpp]({SEARCHFOX_PLACEHOLDER}"
        f"/dom/Gone.cpp).\n\n{MOZREGRESSION_PLACEHOLDER}"
    )
    text = _comment(rec, text=_text(body))["params"]["text"]
    # permalink_hook ran: a real path is pinned, a missing one is unwrapped.
    assert (
        "[MediaDecoder.cpp](https://searchfox.org/firefox-main/rev/abc123/"
        "dom/media/MediaDecoder.cpp#42)"
    ) in text
    assert "`Gone.cpp`" in text and SEARCHFOX_PLACEHOLDER not in text
    # The placeholder hook expanded the block.
    assert MOZREGRESSION_BLOCK in text and MOZREGRESSION_PLACEHOLDER not in text
    # The runtime's reaction footer stays the last part, unchanged.
    assert text.endswith(REACTION_FOOTER)
    # The refusals run too: a second comment is refused.
    with pytest.raises(ToolError):
        _comment(rec)


CAPTURE_LABELS = (
    "Logging presets",
    "Logging output",
    "Logging to the Firefox Profiler",
    "Start Logging",
    "Stop Logging",
    "Upload Local Profile",
)
RETYPED_STEPS = (
    "Open the Logging presets tab and choose Media playback. Then click Start "
    "Logging, do the steps, and click Stop Logging."
)
GRAPHICS_PRESET = (
    "The problem looks like the compositor, so choose the **Graphics** preset in step "
    "2 instead of **Media playback**."
)


def _refused_capture(text):
    rec = _recorder()
    with pytest.raises(ToolError) as e:
        _comment(rec, text=_text(text))
    assert rec.actions == []
    assert TEAM.comment.capture_marker in str(e.value)
    return str(e.value)


def test_the_team_lists_the_capture_labels():
    assert TEAM.comment.capture_labels == CAPTURE_LABELS


def test_retyped_capture_steps_are_refused():
    reason = _refused_capture(RETYPED_STEPS)
    assert (
        "retypes the about:logging capture steps" in reason
        and CAPTURE_PLACEHOLDER in reason
    )


def test_the_block_plus_retyped_steps_is_refused():
    _refused_capture(f"{CAPTURE_PLACEHOLDER}\n\n{RETYPED_STEPS}")
    # Two labels in a sentence beside the block are retyped steps too.
    _refused_capture(
        "On the **Logging presets** tab, choose **Graphics** and then **Start "
        f"Logging** as below.\n\n{CAPTURE_PLACEHOLDER}"
    )


def test_a_list_item_that_names_the_capture_marker_is_refused():
    reason = _refused_capture(
        "Next steps:\n\n1. Open `about:logging` and send a profile."
    )
    assert CAPTURE_PLACEHOLDER in reason
    # Indented inside a list item, or by four spaces, is not verbatim either.
    _refused_capture("1. " + TEAM.comment.capture_block.replace("\n", "\n   "))
    _refused_capture("    " + TEAM.comment.capture_block.replace("\n", "\n    "))


def test_a_list_item_that_names_the_marker_beside_the_block_is_refused():
    reason = _refused_capture(
        "**Next steps**\n\n- An `about:logging` capture, with the steps below.\n\n"
        f"{CAPTURE_PLACEHOLDER}"
    )
    assert "Remove about:logging from the list item; the block names it." in reason


CAPTURE_REQUESTS = (
    "Please capture a log with about:logging.",
    "Could you record a profile with `about:logging`?",
    "Open about:logging and send the profile.",
    "**Open** about:logging and record a profile.",
    "Go to about:logging, then upload it.",
    "Select the preset in about:logging and share the URL.",
    "Please use About:Logging.",
    "Would you mind recording an about:logging profile?",
    "Capture a profile with about:logging and attach it.",
    "Use about:logging to record a profile.",
    "Record a media log in about:logging, then share the URL.",
    "Try a capture with about:logging, and share the URL.",
    "Thanks. Then open about:logging and record the problem.",
    "If it continues, open about:logging and record a profile.",
    "Next, select the Media playback preset in about:logging.",
    "We need an about:logging profile to go further.",
    "It would help to have an about:logging capture.",
    "Could you please record a profile with\nabout:logging?",
    "Open about:config, reset media.foo, then restart. If it continues, capture with "
    "about:logging as below.",
)


@pytest.mark.parametrize("text", CAPTURE_REQUESTS)
def test_a_request_that_names_the_capture_marker_without_the_block_is_refused(text):
    reason = _refused_capture(text)
    assert "asks for about:logging without the capture block" in reason


CAPTURE_CITATIONS = (
    "The attached profile came from `about:logging` and shows the stall. Could you "
    "please attach about:support?",
    "The attached profile came from `about:logging` and shows\nthe stall. Could you "
    "please attach about:support?",
    "Thanks for the about:logging profile in comment 4, please also attach "
    "about:support.",
    "The attached `about:logging` profile shows the stall, so could you please attach "
    "about:support?",
    "Thanks for the profile from\nabout:logging. Please attach about:support.",
    "> Please help, about:logging does not work.\n\nThanks.",
    "> I pressed Start Logging and Stop Logging but nothing uploaded.\n\nThanks.",
)


@pytest.mark.parametrize("body", CAPTURE_CITATIONS)
def test_a_citation_of_an_existing_capture_is_allowed(body):
    rec = _recorder()
    _comment(rec, text=_text(body))
    assert len(rec.actions) == 1


def test_the_graphics_preset_sentence_is_allowed_with_the_block():
    rec = _recorder()
    text = _comment(rec, text=_text(f"{GRAPHICS_PRESET}\n\n{CAPTURE_PLACEHOLDER}"))
    assert TEAM.comment.capture_block in text["params"]["text"]


def test_the_capture_block_keeps_its_loom_video_link():
    assert "https://www.loom.com/share/" in TEAM.comment.capture_block
    rec = _recorder()
    text = _comment(rec, text=_text(f"Thanks.\n\n{CAPTURE_PLACEHOLDER}"))
    assert "https://www.loom.com/share/" in text["params"]["text"]


def test_loom_is_not_an_allowed_link_host():
    assert not any("loom" in host for host in hooks.ALLOWED_LINK_HOSTS)


def test_the_capture_hook_runs_after_the_placeholder_hook():
    names = [h.__name__ for h in _recorder()._hooks["bugzilla.add_comment"]]
    assert (
        names.index("capture_block_hook") == names.index("block_placeholder_hook") + 1
    )


def test_the_capture_and_mozregression_blocks_are_exempt_from_the_limits():
    rec = _recorder()
    body = f"Thanks.\n\n**Next steps**\n\n{TEAM.comment.capture_block}\n\n{MOZREGRESSION_BLOCK}"
    _comment(rec, text=_text(body))
    assert len(rec.actions) == 1


def test_the_placeholders_expand_to_the_verbatim_blocks():
    rec = _recorder()
    body = f"Thanks.\n\n{CAPTURE_PLACEHOLDER}\n\n{MOZREGRESSION_PLACEHOLDER}"
    action = _comment(rec, text=_text(body))
    text = action["params"]["text"]
    assert TEAM.comment.capture_block in text and MOZREGRESSION_BLOCK in text
    assert CAPTURE_PLACEHOLDER not in text and MOZREGRESSION_PLACEHOLDER not in text


def test_a_needs_info_comment_has_no_total_word_budget():
    # Three short paragraphs, 63 words in all, on a comment that asks for something.
    paragraph = " ".join(["word"] * 21) + "."
    rec = _recorder()
    _comment(rec, text=_text("\n\n".join([paragraph] * 3)))
    assert len(rec.actions) == 1


def test_a_paragraph_over_six_sentences_is_refused():
    rec = _recorder()
    with pytest.raises(ToolError) as e:
        _comment(rec, text=_text(" ".join(["Short sentence."] * 7)))
    assert "6" in str(e.value)
    assert rec.actions == []
    # Six is fine, and a refusal does not use up the run's one comment.
    _comment(rec, text=_text(" ".join(["Short sentence."] * 6)))
    assert len(rec.actions) == 1


def test_list_items_do_not_count_as_one_paragraph():
    rec = _recorder()
    steps = "\n".join(f"{n}. Do step {n}." for n in range(1, 9))
    _comment(rec, text=_text(f"Please try this.\n\n{steps}"))
    assert len(rec.actions) == 1


def test_a_sentence_over_twenty_five_words_is_refused():
    rec = _recorder()
    long = " ".join(["word"] * 26) + "."
    with pytest.raises(ToolError) as e:
        _comment(rec, text=_text(long))
    assert "25" in str(e.value)
    # Twenty-five is fine, and a URL's dots do not split or pad a sentence.
    ok = " ".join(["word"] * 24) + " https://bugzilla.mozilla.org/show_bug.cgi?id=1 ok."
    _comment(rec, text=_text(ok))


def test_a_dash_used_as_an_aside_is_refused():
    for body in (
        "The decoder fails - the profile shows it.",
        "The decoder fails -- the profile shows it.",
        "The decoder fails — the profile shows it.",
        "The decoder fails—the profile shows it.",
    ):
        rec = _recorder()
        with pytest.raises(ToolError) as e:
            _comment(rec, text=_text(body))
        assert "dash" in str(e.value)
    # A list marker and a hyphenated word are not asides.
    rec = _recorder()
    _comment(rec, text=_text("- a software-fallback path\n- a second item"))


def test_code_and_quoted_log_lines_are_exempt_from_the_style_limits():
    log = " ".join(["frame"] * 30)
    for body in (
        "The log shows the failure:\n\n```\n[MediaDecoder] D/foo - decode failed\n```",
        f"The log shows it:\n\n~~~text\n{log}\n~~~\n\nThanks.",
        "The pref `media.foo - S2` stays off.",
        "Run this:\n\n  ```sh\n  MOZ_LOG=foo:5 -- firefox\n  ```",
    ):
        rec = _recorder()
        _comment(rec, text=_text(body))
        assert len(rec.actions) == 1, body
    # The prose around the code is still checked.
    rec = _recorder()
    with pytest.raises(ToolError) as e:
        _comment(rec, text=_text("It fails - see:\n\n```\nlog\n```"))
    assert "dash" in str(e.value)


def test_quoted_and_indented_lines_and_link_text_are_exempt_from_the_style_limits():
    long = " ".join(["word"] * 30)
    for body in (
        f"The reporter wrote:\n\n> It stalls - every time. {long}.\n\nThanks.",
        f"The log shows it:\n\n    [Parent] decode - failed {long}\n\nThanks.",
        "See [Bug 1 - S2 crash in the decoder](https://bugzilla.mozilla.org/1).",
        "See [Bug 1 - a stall][1].\n\n[1]: https://bugzilla.mozilla.org/1",
    ):
        rec = _recorder()
        _comment(rec, text=_text(body))
        assert len(rec.actions) == 1, body
    rec = _recorder()
    with pytest.raises(ToolError) as e:
        _comment(rec, text=_text("It fails - see:\n\n> It stalls."))
    assert "dash" in str(e.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://bugzilla.mozilla.org/show_bug.cgi?id=1",
        "https://mozilla.org/",
        "https://hg.mozilla.net/x",
        "https://searchfox.org/firefox-main/source/dom/A.cpp",
        "https://profiler.firefox.com/public/x",
        "https://share.firefox.dev/abc",
        "https://firefox-source-docs.mozilla.org/media/",
        "https://developer.mozilla.org/en-US/docs/Web/API",
        "https://www.w3.org/TR/media-source-2/",
        "https://html.spec.whatwg.org/#media",
        "https://w3c.github.io/mediacapture-main/",
        "https://wicg.github.io/x/",
        "https://github.com/w3c/media-source/issues/1",
        "https://tc39.es/ecma262/",
        "https://www.rfc-editor.org/rfc/rfc6716",
        "https://datatracker.ietf.org/doc/x",
        "https://issues.chromium.org/issues/1",
        "https://bugs.webkit.org/show_bug.cgi?id=1",
        "HTTPS://Bugzilla.Mozilla.org/x",
    ],
)
def test_a_link_to_an_allowed_host_is_kept(url):
    rec = _recorder()
    _comment(rec, text=_text(f"See [the page]({url}) and {url} for more."))
    assert len(rec.actions) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x",
        "http://mozilla.org.evil.example/x",
        "https://evilmozilla.org/x",
        "https://searchfox.org@evil.example/x",
        "https://evil.github.io/x",
        "https://gist.github.com.evil.example/x",
        "//evil.example/x",
        "https://www.loom.com/share/x",
        "https://loom.com/share/x",
    ],
)
def test_a_link_to_any_other_host_is_refused(url):
    rec = _recorder()
    with pytest.raises(ToolError) as e:
        _comment(rec, text=_text(f"See [the page]({url})."))
    assert "host" in str(e.value)
    assert rec.actions == []


@pytest.mark.parametrize(
    "body",
    [
        "See https://evil.com\\@searchfox.org/x now.",
        "See https://evil.com:443@github.com/ now.",
        "See https://searchfox.org'@evil.example/x now.",
        'See https://searchfox.org"@evil.example/x now.',
        "See https://searchfox.org]@evil.example/x now.",
        "See https://searchfox.org)@evil.example/x now.",
        "See https://searchfox.org[@evil.example/x now.",
        "See [x](http:evil.example) now.",
        "See [x](https:\\\\evil.com) now.",
        "See [x](<https://evil.example/x>) now.",
        "See [x]( //evil.example/x) now.",
        "Open www.evil.com now.",
        "See [the note][1].\n\n[1]: //evil.com",
        "See [the note][1].\n\n   [1]: <https://evil.com/x> 'title'",
        'See <a href="//evil.com">the page</a>.',
        "See <A\nHREF='https://evil.com'>the page</A>.",
        '<img src="https://evil.com/x.png">',
        "See [x](ftp://evil.com/) now.",
        "See [x](javascript:alert(1)) now.",
        "See [x](data:text/html,x) now.",
        "See [x](mailto:a@evil.com) now.",
        "Write to <mailto:a@evil.com> now.",
        "Fetch ftp://evil.com/x now.",
        "See <https://a`@evil.com> then `.mozilla.org` now.",
        "See _https://evil.com_ now.",
        "See <b [x](//evil.com) > now.",
        "See [the note][1].\n\n[1]:\n//evil.com",
        "Run it:\n\n```\ncode\n````\n\nSee [x](https://evil.com).\n\n```\nx\n```",
        "Run it:\n\n    ```\nSee [x](https://evil.com).\n\n```\nx\n```",
    ],
)
def test_every_markdown_link_form_is_checked(body):
    rec = _recorder()
    with pytest.raises(ToolError) as e:
        _comment(rec, text=_text(body))
    assert "link" in str(e.value)
    assert rec.actions == []


@pytest.mark.parametrize(
    "body",
    [
        "See <https://searchfox.org> now.",
        "See (https://searchfox.org) now.",
        "See https://searchfox.org/x, then retry.",
        "See www.w3.org/TR/x now.",
        "See [the note][1].\n\n[1]: https://searchfox.org/x",
        "See [x](https://searchfox.org/a_(b)) now.",
        "Open about:support and copy it.",
    ],
)
def test_the_allowed_link_forms_are_kept(body):
    rec = _recorder()
    _comment(rec, text=_text(body))
    assert len(rec.actions) == 1


def test_a_link_in_code_is_a_quote_and_not_checked():
    rec = _recorder()
    body = (
        "Open `https://evil.example/x` now.\n\n"
        "```\n[Parent] GET https://evil.example/y failed\n```"
    )
    _comment(rec, text=_text(body))
    assert len(rec.actions) == 1
    with pytest.raises(ToolError):
        _comment(_recorder(), text=_text(f"{body}\n\nSee https://evil.example/z now."))


def test_the_permalink_placeholder_and_the_quoted_blocks_are_allowed():
    rec = _recorder()
    body = (
        f"See [A.cpp]({SEARCHFOX_PLACEHOLDER}/dom/A.cpp#1).\n\n"
        f"{CAPTURE_PLACEHOLDER}\n\n{MOZREGRESSION_PLACEHOLDER}"
    )
    _comment(rec, text=_text(body))
    assert len(rec.actions) == 1


def test_a_severity_or_priority_label_in_the_prose_is_refused():
    for body in (
        "Marking this S2.",
        "This looks like a P1 to me.",
        "Raising to S3/P2.",
    ):
        rec = _recorder()
        with pytest.raises(ToolError) as e:
            _comment(rec, text=_text(body))
        assert "severity or priority" in str(e.value)
    rec = _recorder()
    _comment(
        rec, text=_text("Peer-to-peer P2P streams and the S1E3 episode play fine.")
    )


# --- the field change ------------------------------------------------------- #


def test_a_field_change_before_the_comment_is_refused(new_state):
    rec = _recorder(new_state)
    with pytest.raises(ToolError) as e:
        _update(rec, {"severity": "S3"})
    assert "comment first" in str(e.value)
    assert rec.actions == []


def test_a_field_change_against_another_bug_is_refused(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    with pytest.raises(ToolError):
        _update(rec, {"severity": "S3"}, bug_id=1)
    assert len(rec.actions) == 1


def test_severity_and_priority_are_set_when_unset(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    _update(rec, {"severity": " s3", "priority": "p2"})
    assert rec.actions[1]["params"]["changes"] == {"severity": "S3", "priority": "P2"}


def test_a_set_severity_or_priority_is_never_overwritten(bug_new, comments_new):
    state = TriageState(
        bug=dict(bug_new, severity="S2", priority="P3"), comments=comments_new, now=NOW
    )
    rec = _recorder(state)
    _comment(rec)
    for changes in ({"severity": "S3"}, {"priority": "P2"}):
        with pytest.raises(ToolError) as e:
            _update(rec, changes)
        assert "already" in str(e.value)
    assert len(rec.actions) == 1


def test_s1_is_never_set(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, {"severity": "S1"})
    assert "never set S1" in str(e.value)


@pytest.mark.parametrize("bug_type", ["task", "enhancement", "Enhancement"])
def test_no_severity_on_a_task_or_enhancement(new_state, bug_type):
    state = TriageState(
        bug=dict(new_state.bug, type=bug_type), comments=new_state.comments, now=NOW
    )
    rec = _recorder(state)
    _comment(rec)
    with pytest.raises(ToolError, match="severity is N/A"):
        _update(rec, {"severity": "S4"})
    action = _update(rec, {"priority": "P3"})
    assert action["params"]["changes"] == {"priority": "P3"}


def test_a_defect_may_get_a_severity(new_state):
    state = TriageState(
        bug=dict(new_state.bug, type="defect"), comments=new_state.comments, now=NOW
    )
    rec = _recorder(state)
    _comment(rec)
    assert _update(rec, {"severity": "S3"})["params"]["changes"]["severity"] == "S3"


def test_the_agent_may_set_only_p1_to_p3():
    assert {"P1", "P2", "P3"} == TRIAGE_PRIORITIES


@pytest.mark.parametrize(
    "level, reason",
    [("P4", "reserved for web platform test bots"), ("p5", "a person's decision")],
)
def test_p4_and_p5_are_never_set(new_state, level, reason):
    rec = _recorder(new_state)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, {"priority": level})
    assert reason in str(e.value)
    assert len(rec.actions) == 1
    _update(rec, {"priority": "P3"})
    assert rec.actions[1]["params"]["changes"] == {"priority": "P3"}


def test_an_unknown_level_is_refused(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    for changes in (
        {"severity": "S5"},
        {"severity": "critical"},
        {"priority": "P0"},
        {"priority": 2},
    ):
        with pytest.raises(ToolError):
            _update(rec, changes)
    assert len(rec.actions) == 1


def test_every_other_field_is_refused_by_name(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    for changes in (
        {"assigned_to": "dev@example.org"},
        {"cf_qa_whiteboard": "[x]"},
        {"severity": "S3", "cc": {"add": ["x@example.com"]}},
    ):
        with pytest.raises(ToolError) as e:
            _update(rec, changes)
        assert "may not change" in str(e.value)
    for changes in ({}, None, "S3"):
        with pytest.raises(ToolError):
            _update(rec, changes)
    assert len(rec.actions) == 1


# --- blocks, keywords, component, resolution -------------------------------- #


def test_a_known_meta_bug_may_be_added_to_blocks(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    _update(rec, {"blocks": {"add": [KNOWN_META]}})
    assert len(rec.actions) == 2


def test_blocks_adds_at_most_two_meta_bugs(new_state):
    metas = sorted(TEAM_META_BUGS)
    rec = _recorder(new_state)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, {"blocks": {"add": metas[:3]}})
    assert "2" in str(e.value)
    assert len(rec.actions) == 1
    _update(rec, {"blocks": {"add": metas[:2]}})
    assert len(rec.actions) == 2


def test_blocks_only_adds_a_known_meta_bug_once(bug_new, comments_new):
    state = TriageState(
        bug=dict(bug_new, blocks=[KNOWN_META]), comments=comments_new, now=NOW
    )
    rec = _recorder(state)
    _comment(rec)
    other = sorted(TEAM_META_BUGS)[1]
    for changes in (
        {"blocks": {"add": [1]}},
        {"blocks": {"add": [KNOWN_META]}},
        {"blocks": {"remove": [KNOWN_META]}},
        {"blocks": [other]},
        {"blocks": {"add": [str(other)]}},
        {"blocks": {"add": []}},
    ):
        with pytest.raises(ToolError):
            _update(rec, changes)
    _update(rec, {"blocks": {"add": [other]}})
    assert len(rec.actions) == 2


def test_only_an_allowed_keyword_may_be_added(bug_new, comments_new):
    state = TriageState(
        bug=dict(bug_new, keywords=["stalled"]), comments=comments_new, now=NOW
    )
    rec = _recorder(state)
    _comment(rec)
    for changes in (
        {"keywords": {"add": ["crash"]}},
        {"keywords": {"add": ["stalled"]}},
        {"keywords": {"remove": ["stalled"]}},
        {"keywords": ["regression"]},
    ):
        with pytest.raises(ToolError):
            _update(rec, changes)
    _update(rec, {"keywords": {"add": ["regression"]}})
    assert len(rec.actions) == 2


def test_a_bug_may_move_to_another_component(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    for changes in (
        {"product": "Core"},
        {"component": new_state.bug["component"]},
        {"product": new_state.bug["product"], "component": new_state.bug["component"]},
        {"component": ""},
    ):
        with pytest.raises(ToolError):
            _update(rec, changes)
    _update(rec, {"product": "Core", "component": "Graphics"})
    assert len(rec.actions) == 2


@pytest.mark.parametrize("resolution", ["INVALID", "WONTFIX", "FIXED"])
def test_a_bug_may_be_resolved(new_state, resolution):
    rec = _recorder(new_state, close_verdicts=_verdicts((resolution, None)))
    _comment(rec)
    _update(rec, {"status": "RESOLVED", "resolution": resolution})
    assert len(rec.actions) == 2


def test_a_duplicate_names_another_bug(new_state):
    rec = _recorder(new_state, close_verdicts=_verdicts(("DUPLICATE", 1)))
    _comment(rec)
    for changes in (
        {"status": "RESOLVED", "resolution": "DUPLICATE"},
        {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": BUG},
        {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": "1"},
        {"dupe_of": 1},
        {"status": "RESOLVED", "resolution": "INVALID", "dupe_of": 1},
    ):
        with pytest.raises(ToolError):
            _update(rec, changes)
    _update(rec, {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": 1})
    assert len(rec.actions) == 2


def test_the_duplicate_target_may_get_the_team_meta_bug(new_state):
    rec = _dupe_recorder(new_state)
    _duplicate_of_target(rec)
    _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    assert [a["params"]["bug_id"] for a in rec.actions] == [BUG, BUG, DUPE_TARGET]


def test_a_meta_bug_on_another_bug_needs_the_duplicate_first(new_state):
    rec = _recorder(new_state, close_verdicts=_verdicts(("INVALID", None)))
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    assert "duplicate" in str(e.value)
    _update(rec, {"status": "RESOLVED", "resolution": "INVALID"})
    with pytest.raises(ToolError):
        _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    assert len(rec.actions) == 2


def test_a_duplicate_of_a_meta_bug_is_refused(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(
            rec,
            {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": KNOWN_META},
        )
    assert "meta bug" in str(e.value)
    assert len(rec.actions) == 1


CANDIDATE = 9000005


def _duplicate(rec, dupe_of):
    return _update(
        rec, {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": dupe_of}
    )


def test_a_duplicate_of_a_meta_candidate_is_refused_and_keeps_the_allowance(
    new_state,
):
    rec = _dupe_recorder(new_state, meta_candidates=frozenset({CANDIDATE}))
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _duplicate(rec, CANDIDATE)
    assert "meta bug" in str(e.value) and str(CANDIDATE) in str(e.value)
    assert len(rec.actions) == 1
    _duplicate(rec, DUPE_TARGET)
    assert len(rec.actions) == 2


def test_a_meta_candidate_found_after_the_hooks_start_is_refused(new_state):
    found = set()
    rec = _recorder(new_state, meta_candidates=found)
    _comment(rec)
    found.add(CANDIDATE)
    with pytest.raises(ToolError) as e:
        _duplicate(rec, CANDIDATE)
    assert "meta bug" in str(e.value)
    assert len(rec.actions) == 1


def test_a_duplicate_of_a_bug_this_bug_blocks_is_refused(bug_new, comments_new):
    state = TriageState(
        bug=dict(bug_new, blocks=[CANDIDATE]), comments=comments_new, now=NOW
    )
    rec = _recorder(state)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _duplicate(rec, CANDIDATE)
    assert "blocks" in str(e.value)
    assert len(rec.actions) == 1


def test_a_duplicate_of_a_plain_candidate_passes(new_state):
    rec = _dupe_recorder(new_state, meta_candidates=frozenset({CANDIDATE}))
    _comment(rec)
    _duplicate(rec, DUPE_TARGET)
    assert len(rec.actions) == 2


def test_a_duplicate_does_not_block_the_meta_bug_itself(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(
            rec,
            {
                "status": "RESOLVED",
                "resolution": "DUPLICATE",
                "dupe_of": DUPE_TARGET,
                "blocks": {"add": [KNOWN_META]},
            },
        )
    assert str(DUPE_TARGET) in str(e.value)
    assert len(rec.actions) == 1


@pytest.mark.parametrize("bug_id", [str(DUPE_TARGET), True, 0])
def test_an_invalid_other_bug_id_is_refused_as_another_bug(new_state, bug_id):
    rec = _dupe_recorder(new_state)
    _duplicate_of_target(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=bug_id)
    assert "record actions against that bug" in str(e.value)
    assert len(rec.actions) == 2


def test_the_duplicate_target_refuses_two_meta_bugs(new_state):
    rec = _dupe_recorder(new_state)
    _duplicate_of_target(rec)
    with pytest.raises(ToolError) as e:
        _update(
            rec, {"blocks": {"add": sorted(TEAM_META_BUGS)[:2]}}, bug_id=DUPE_TARGET
        )
    assert "one meta bug" in str(e.value)
    assert len(rec.actions) == 2


def test_the_duplicate_target_gets_only_one_team_meta_bug(new_state):
    rec = _dupe_recorder(new_state)
    _duplicate_of_target(rec)
    for bug_id, changes in (
        (DUPE_TARGET + 1, {"blocks": {"add": [KNOWN_META]}}),
        (DUPE_TARGET, {"blocks": {"add": [1]}}),
        (DUPE_TARGET, {"blocks": {"add": sorted(TEAM_META_BUGS)[:2]}}),
        (DUPE_TARGET, {"blocks": {"add": [KNOWN_META]}, "severity": "S3"}),
        (DUPE_TARGET, {"severity": "S3"}),
        (DUPE_TARGET, {"blocks": {"remove": [KNOWN_META]}}),
        (DUPE_TARGET, {"blocks": {"set": [KNOWN_META]}}),
        (DUPE_TARGET, ["blocks"]),
    ):
        with pytest.raises(ToolError):
            _update(rec, changes, bug_id=bug_id)
    _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    with pytest.raises(ToolError):
        _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    assert len(rec.actions) == 3


def _ni(requestee):
    return {"flags": [{"name": "needinfo", "status": "?", "requestee": requestee}]}


def _people_state(bug_new, comments_new, *cc):
    return TriageState(bug=dict(bug_new, cc=list(cc)), comments=comments_new, now=NOW)


def test_a_needinfo_goes_to_someone_on_the_bug(bug_new, comments_new):
    rec = _recorder(_people_state(bug_new, comments_new, "watcher@example.com"))
    _comment(rec)
    for requestee in ("", "not-an-email", "owner@example.org"):
        with pytest.raises(ToolError):
            _update(rec, _ni(requestee))
    _update(rec, _ni("Watcher@Example.com"))
    assert len(rec.actions) == 2


def test_a_needinfo_off_the_bug_needs_a_move(bug_new, comments_new):
    # On a move, the target team's developer may be asked, on the bug or not.
    rec = _recorder(_people_state(bug_new, comments_new, "watcher@example.com"))
    _comment(rec)
    flags = _ni("watcher@example.com")["flags"] + _ni("owner@example.org")["flags"]
    with pytest.raises(ToolError) as e:
        _update(rec, {"flags": flags})
    assert "owner@example.org" in str(e.value)
    _update(rec, {"component": "Graphics", "flags": flags})
    assert len(rec.actions) == 2


# --- the early ask on the reporter ------------------------------------------ #

REPORTER = "reporter@example.com"


def _ask_recorder(bug_new, comments_new, investigations, **bug):
    """No needinfo is open, so the reporter may be asked."""
    state = TriageState(
        bug={**bug_new, "flags": [], **bug}, comments=comments_new, now=NOW
    )
    return _recorder(state, investigations=investigations)


def _spawn(store, subagent_type):
    note_spawn(
        store, {"tool_name": "Agent", "tool_input": {"subagent_type": subagent_type}}
    )


def test_the_first_reporter_needinfo_before_an_investigation_is_refused(
    bug_new, comments_new
):
    store = Investigations()
    rec = _ask_recorder(bug_new, comments_new, store)
    _comment(rec)
    with pytest.raises(ToolError, match="spawn bug_investigator first") as e:
        _update(rec, _ni(REPORTER))
    assert "only when the bug has no data to analyze" in str(e.value)
    assert len(rec.actions) == 1
    assert store.refusals == 1


def test_the_second_reporter_needinfo_passes_and_the_refusal_used_no_allowance(
    bug_new, comments_new
):
    store = Investigations()
    rec = _ask_recorder(bug_new, comments_new, store)
    _comment(rec)
    with pytest.raises(ToolError):
        _update(
            rec, {"flags": _ni("watcher@example.com")["flags"] + _ni(REPORTER)["flags"]}
        )
    _update(rec, _ni(REPORTER))
    assert len(rec.actions) == 2
    assert store == Investigations(spawns=0, refusals=1)


def test_a_reporter_needinfo_after_an_investigation_is_not_refused(
    bug_new, comments_new
):
    store = Investigations()
    _spawn(store, "bug_investigator")
    rec = _ask_recorder(bug_new, comments_new, store)
    _comment(rec)
    _update(rec, _ni(REPORTER))
    assert len(rec.actions) == 2
    assert store == Investigations(spawns=1, refusals=0)


def test_a_needinfo_on_a_developer_or_an_expert_is_never_refused(bug_new, comments_new):
    expert = sorted(e.email for e in TEAM.needinfo_experts)[0]
    for requestee in ("watcher@example.com", "dev@example.org", expert):
        store = Investigations()
        rec = _ask_recorder(bug_new, comments_new, store)
        _comment(rec)
        _update(rec, _ni(requestee))
        assert store.refusals == 0


@pytest.mark.parametrize("reporter", ["dev@mozilla.com", "expert"])
def test_a_reporter_who_is_a_developer_or_an_expert_is_never_refused(
    bug_new, comments_new, reporter
):
    if reporter == "expert":
        reporter = sorted(e.email for e in TEAM.needinfo_experts)[0]
    store = Investigations()
    rec = _ask_recorder(
        bug_new, comments_new, store, creator=reporter, creator_detail={}
    )
    _comment(rec)
    _update(rec, _ni(reporter))
    assert store.refusals == 0


def test_only_a_bug_investigator_spawn_counts(bug_new, comments_new):
    store = Investigations()
    for other in ("log_analyst", "duplicate_hunter", "close_verifier", None):
        _spawn(store, other)
    note_spawn(store, {"tool_name": "Agent"})
    assert store.spawns == 0
    rec = _ask_recorder(bug_new, comments_new, store)
    _comment(rec)
    with pytest.raises(ToolError, match="spawn bug_investigator first"):
        _update(rec, _ni(REPORTER))


AUTHOR = "author@example.org"


REGRESSION = {"keywords": {"add": ["regression"]}}


def _author_recorder(bug_new, comments_new, authors, *cc, **bug):
    state = TriageState(
        bug={**bug_new, "cc": list(cc), "keywords": [], "regressed_by": [], **bug},
        comments=comments_new,
        now=NOW,
    )
    return _recorder(state, commit_authors=authors)


def test_a_commit_author_may_be_asked_for_a_restricted_regressor(bug_new, comments_new):
    authors = set()
    rec = _author_recorder(bug_new, comments_new, authors)
    _comment(rec)
    with pytest.raises(ToolError, match="not on the team's expert list"):
        _update(rec, {**REGRESSION, **_ni(AUTHOR)})
    assert len(rec.actions) == 1
    # The hook reads the run's set at each check, as the vcs hook fills it.
    authors.add(AUTHOR)
    action = _update(rec, {**REGRESSION, **_ni(" Author@Example.org ")})
    assert action["params"]["changes"]["flags"][0]["requestee"] == AUTHOR


def test_the_mozilla_assignee_of_a_commit_bug_may_be_asked(bug_new, comments_new):
    from hackbot_agents.gecko_triage.authors import CommitFacts

    facts = CommitFacts()
    facts.add({AUTHOR}, {9000003: {"jane dev"}, 9000004: {"sheriff"}})
    rec = _author_recorder(bug_new, comments_new, facts.authors)
    _comment(rec)
    with pytest.raises(ToolError, match="not on the team's expert list"):
        _update(rec, _ni("dev@mozilla.com"))
    facts.add(
        assignees={
            9000003: ("dev@mozilla.com", "jane dev"),
            9000004: ("other@mozilla.com", "other dev"),
        }
    )
    with pytest.raises(ToolError, match="not on the team's expert list"):
        _update(rec, _ni("other@mozilla.com"))
    action = _update(rec, _ni("dev@mozilla.com"))
    assert action["params"]["changes"]["flags"][0]["requestee"] == "dev@mozilla.com"


@pytest.mark.parametrize(
    ("bug", "changes"),
    [
        ({"keywords": ["regression"]}, {}),
        ({}, {**REGRESSION, "regressed_by": {"add": [9000003]}}),
        ({"keywords": ["regression"], "regressed_by": [9000003]}, {}),
        # The "No matching expert" case: no regression, the history names the developer.
        ({}, {}),
    ],
    ids=["regression-keyword", "regressed-by-change", "has-regressed-by", "history"],
)
def test_a_commit_author_the_history_names_may_be_asked(
    bug_new, comments_new, bug, changes
):
    rec = _author_recorder(bug_new, comments_new, {AUTHOR}, **bug)
    _comment(rec)
    action = _update(rec, {**changes, **_ni(AUTHOR)})
    assert action["params"]["changes"]["flags"][0]["requestee"] == AUTHOR


def test_author_needinfos_names_only_the_people_that_commit_authors_made_askable(
    bug_new, comments_new
):
    state = _people_state(bug_new, comments_new, "watcher@example.com")
    actions = [
        {"type": "bugzilla.add_comment", "params": {"text": "x"}},
        {
            "type": "bugzilla.update_bug",
            "params": {
                "changes": {
                    "flags": _ni(AUTHOR)["flags"] + _ni("watcher@example.com")["flags"]
                }
            },
        },
    ]
    authors = {AUTHOR, "watcher@example.com"}
    assert hooks.author_needinfos(actions, state, authors) == [AUTHOR]
    assert hooks.author_needinfos(actions, state, set()) == []


def test_a_commit_author_on_the_bug_is_asked_as_before(bug_new, comments_new):
    rec = _author_recorder(bug_new, comments_new, {AUTHOR}, AUTHOR)
    _comment(rec)
    action = _update(rec, {"regressed_by": {"add": [9000003]}, **_ni(AUTHOR)})
    assert action["params"]["changes"]["flags"][0]["requestee"] == AUTHOR


def _set_experts(monkeypatch, emails):
    for module in (hooks, state_module):
        monkeypatch.setattr(module, "NEEDINFO_EXPERTS", frozenset(emails))


def _set_fallback(monkeypatch, emails):
    for module in (hooks, state_module):
        monkeypatch.setattr(module, "NEEDINFO_FALLBACK", frozenset(emails))


def test_a_commit_author_does_not_lift_the_one_fallback_person_limit(
    bug_new, comments_new, monkeypatch
):
    _set_fallback(monkeypatch, TWO_FALLBACK)
    rec = _recorder(
        _people_state(bug_new, comments_new), commit_authors=set(TWO_FALLBACK)
    )
    _comment(rec)
    flags = [_ni(e)["flags"][0] for e in TWO_FALLBACK]
    with pytest.raises(ToolError, match="only one person from the fallback list"):
        _update(rec, {"flags": flags})
    assert len(rec.actions) == 1


EXPERT = "expert@example.org"


@pytest.fixture
def expert(monkeypatch):
    _set_experts(monkeypatch, {EXPERT})
    return EXPERT


def test_a_listed_expert_may_be_asked_without_a_move(bug_new, comments_new, expert):
    rec = _recorder(_people_state(bug_new, comments_new))
    _comment(rec)
    action = _update(rec, _ni(" Expert@Example.org "))
    assert action["params"]["changes"]["flags"][0]["requestee"] == expert
    assert "component" not in action["params"]["changes"]


def test_someone_off_the_bug_and_off_the_expert_list_is_refused(
    bug_new, comments_new, expert
):
    rec = _recorder(_people_state(bug_new, comments_new))
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, _ni("owner@example.org"))
    assert "expert list" in str(e.value)
    assert len(rec.actions) == 1
    _update(rec, _ni(expert))
    assert len(rec.actions) == 2


def test_a_listed_expert_with_an_open_needinfo_is_refused(
    bug_new, comments_new, expert
):
    flag = dict(bug_new["flags"][0], id=900002, requestee=expert)
    bug = dict(bug_new, flags=[*bug_new["flags"], flag])
    rec = _recorder(TriageState(bug=bug, comments=comments_new, now=NOW))
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, _ni(expert))
    assert "already has an open needinfo" in str(e.value)
    assert len(rec.actions) == 1


def test_a_listed_expert_counts_toward_the_cap_and_is_asked_once(
    bug_new, comments_new, expert
):
    people = [f"p{n}@example.com" for n in range(3)]
    state = _people_state(bug_new, comments_new, *people)
    rec = _recorder(state)
    _comment(rec)
    for flags in (
        _ni(expert)["flags"] + _ni(expert.upper())["flags"],
        [_ni(p)["flags"][0] for p in [*people, expert]],
    ):
        with pytest.raises(ToolError):
            _update(rec, {"flags": flags})
    assert len(rec.actions) == 1
    fresh = _recorder(state)
    _comment(fresh)
    _update(fresh, {"flags": [_ni(p)["flags"][0] for p in people]})
    assert len(fresh.actions) == 2


def test_a_listed_expert_may_be_asked_with_a_move(bug_new, comments_new, expert):
    rec = _recorder(_people_state(bug_new, comments_new))
    _comment(rec)
    action = _update(rec, {"component": "Graphics", **_ni(expert)})
    assert action["params"]["changes"]["flags"][0]["requestee"] == expert


def test_the_bot_is_never_a_listed_expert(bug_new, comments_new, monkeypatch):
    _set_experts(monkeypatch, {BOT_ACCOUNT.lower()})
    rec = _recorder(_people_state(bug_new, comments_new))
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, _ni(BOT_ACCOUNT))
    assert "bot" in str(e.value)
    assert len(rec.actions) == 1


def test_the_hook_reads_the_run_teams_experts_and_fallback_list():
    assert {e.email for e in TEAM.needinfo_experts} == hooks.NEEDINFO_EXPERTS
    assert set(TEAM.needinfo_fallback) == hooks.NEEDINFO_FALLBACK


TWO_FALLBACK = ("fallback-a@example.com", "fallback-b@example.com")


def test_two_fallback_people_in_one_run_are_refused(bug_new, comments_new, monkeypatch):
    _set_fallback(monkeypatch, TWO_FALLBACK)
    rec = _recorder(_people_state(bug_new, comments_new))
    _comment(rec)
    flags = [_ni(e)["flags"][0] for e in TWO_FALLBACK]
    with pytest.raises(ToolError, match="only one person from the fallback list"):
        _update(rec, {"flags": flags})
    assert len(rec.actions) == 1


def test_a_fallback_person_already_on_the_bug_does_not_count(
    bug_new, comments_new, monkeypatch
):
    _set_fallback(monkeypatch, TWO_FALLBACK)
    rec = _recorder(_people_state(bug_new, comments_new, TWO_FALLBACK[0]))
    _comment(rec)
    action = _update(rec, {"flags": [_ni(e)["flags"][0] for e in TWO_FALLBACK]})
    assert len(action["params"]["changes"]["flags"]) == 2


def test_experts_who_are_also_on_the_fallback_list_do_not_count(bug_new, comments_new):
    both = sorted(hooks.NEEDINFO_FALLBACK & hooks.NEEDINFO_EXPERTS)
    assert len(both) >= 2
    rec = _recorder(_people_state(bug_new, comments_new))
    _comment(rec)
    action = _update(rec, {"flags": [_ni(e)["flags"][0] for e in both[:2]]})
    assert len(action["params"]["changes"]["flags"]) == 2


def test_a_fallback_person_may_be_asked_without_a_move(bug_new, comments_new):
    (fallback,) = set(TEAM.needinfo_fallback) - {e.email for e in TEAM.needinfo_experts}
    rec = _recorder(_people_state(bug_new, comments_new))
    _comment(rec)
    action = _update(rec, _ni(fallback))
    assert action["params"]["changes"]["flags"][0]["requestee"] == fallback


def test_someone_on_no_list_is_still_refused_without_a_move(bug_new, comments_new):
    rec = _recorder(_people_state(bug_new, comments_new))
    _comment(rec)
    with pytest.raises(ToolError, match="expert list"):
        _update(rec, _ni("someone@mozilla.com"))
    assert len(rec.actions) == 1


@pytest.mark.parametrize(
    "move",
    [
        lambda bug: {"component": bug["component"].upper()},
        lambda bug: {"component": f" {bug['component']} "},
        lambda bug: {"product": bug["product"].lower(), "component": bug["component"]},
    ],
)
def test_a_move_to_the_current_component_in_another_case_is_refused(
    bug_new, comments_new, move
):
    # A move that is not one must not unlock a needinfo on someone off the bug.
    state = _people_state(bug_new, comments_new)
    rec = _recorder(state)
    _comment(rec)
    for changes in (move(state.bug), {**move(state.bug), **_ni("owner@example.org")}):
        with pytest.raises(ToolError):
            _update(rec, changes)
    assert len(rec.actions) == 1


def test_a_move_is_recorded_without_surrounding_spaces(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    action = _update(rec, {"product": " Core ", "component": " Graphics "})
    changes = action["params"]["changes"]
    assert (changes["product"], changes["component"]) == ("Core", "Graphics")


def test_a_move_names_the_product_and_component_as_text(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    for changes in ({"product": 1, "component": "Graphics"}, {"component": ["x"]}):
        with pytest.raises(ToolError):
            _update(rec, changes)
    assert len(rec.actions) == 1


def test_the_needinfo_requestee_is_recorded_normalized(bug_new, comments_new):
    rec = _recorder(_people_state(bug_new, comments_new, "watcher@example.com"))
    _comment(rec)
    action = _update(rec, _ni(" Watcher@Example.com "))
    assert action["params"]["changes"]["flags"][0]["requestee"] == "watcher@example.com"


def test_every_recorded_needinfo_asks_bugzilla_for_a_new_flag(bug_new, comments_new):
    people = [f"p{n}@example.com" for n in range(3)]
    rec = _recorder(_people_state(bug_new, comments_new, *people))
    _comment(rec)
    action = _update(rec, {"flags": [_ni(p)["flags"][0] for p in people]})
    assert action["params"]["changes"]["flags"] == [
        {"name": "needinfo", "status": "?", "requestee": p, "new": True} for p in people
    ]


@pytest.mark.parametrize(
    "requestee",
    [
        "a@example.com,b@example.com",
        "a@example.com b@example.com",
        "a@example.com; b@example.com",
        "@example.com",
        "a@",
        "a@example",
        "a@@example.com",
        "<a@example.com>",
    ],
)
def test_a_needinfo_names_exactly_one_email(bug_new, comments_new, requestee):
    rec = _recorder(_people_state(bug_new, comments_new, "a@example.com"))
    _comment(rec)
    with pytest.raises(ToolError):
        _update(rec, {"component": "Graphics", **_ni(requestee)})
    assert len(rec.actions) == 1


def test_a_needinfo_names_each_person_once(bug_new, comments_new):
    rec = _recorder(_people_state(bug_new, comments_new, "watcher@example.com"))
    _comment(rec)
    flags = _ni("watcher@example.com")["flags"] + _ni(" WATCHER@example.com")["flags"]
    with pytest.raises(ToolError) as e:
        _update(rec, {"flags": flags})
    assert "once" in str(e.value)
    assert len(rec.actions) == 1


def test_at_most_three_needinfos_per_run(bug_new, comments_new):
    people = [f"p{n}@example.com" for n in range(4)]
    rec = _recorder(_people_state(bug_new, comments_new, *people))
    _comment(rec)
    flags = [_ni(p)["flags"][0] for p in people]
    with pytest.raises(ToolError) as e:
        _update(rec, {"flags": flags})
    assert "3" in str(e.value)
    _update(rec, {"flags": flags[:3]})
    assert len(rec.actions) == 2


def test_every_field_change_goes_in_one_update(new_state):
    # The apply step merges updates field by field, so a second update would replace
    # the first one's flags or blocks.
    rec = _recorder(new_state)
    _comment(rec)
    _update(rec, {"severity": "S3"})
    with pytest.raises(ToolError) as e:
        _update(rec, {"priority": "P3"})
    assert "one" in str(e.value)
    assert len(rec.actions) == 2


def test_a_move_to_another_product_carries_its_version_and_milestone(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    action = _update(rec, {"product": "Firefox", "component": "General"})
    changes = action["params"]["changes"]
    assert changes["version"] == "unspecified"
    assert changes["target_milestone"] == "---"


def test_version_and_milestone_are_never_the_models_to_set(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    for changes in (
        {"target_milestone": "150 Branch"},
        {"version": "Trunk"},
        {"product": "Firefox", "component": "General", "version": "Trunk"},
    ):
        with pytest.raises(ToolError) as e:
            _update(rec, changes)
        assert "may not change" in str(e.value)
    assert len(rec.actions) == 1


def test_a_comment_may_name_about_logging_when_it_asks_for_nothing(new_state):
    rec = _recorder(new_state)
    body = "The attached profile came from about:logging and shows the stall."
    _comment(rec, text=_text(body))
    assert len(rec.actions) == 1


@pytest.mark.parametrize("product", [None, str.upper], ids=["component", "upper"])
def test_a_move_within_the_product_changes_nothing_else(new_state, product):
    rec = _recorder(new_state)
    _comment(rec)
    changes = {"component": "Graphics"}
    if product:
        changes["product"] = product(new_state.bug["product"])
    action = _update(rec, changes)
    assert set(action["params"]["changes"]) == set(changes)


def test_a_needinfo_on_someone_who_already_has_one_is_refused(new_state):
    # The developer's needinfo on the reporter is open in the fixture.
    rec = _recorder(new_state)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, _ni("reporter@example.com"))
    assert "already has an open needinfo" in str(e.value)


def test_a_needinfo_on_the_bot_is_refused(bug_new, comments_new):
    state = TriageState(
        bug=dict(bug_new, cc=[*bug_new["cc"], BOT_ACCOUNT]),
        comments=comments_new,
        now=NOW,
    )
    rec = _recorder(state)
    _comment(rec)
    with pytest.raises(ToolError):
        _update(rec, _ni(BOT_ACCOUNT))


def test_only_a_needinfo_request_flag_may_be_set(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    for flags in (
        [{"name": "needinfo", "status": "-", "requestee": "watcher@example.com"}],
        [{"name": "needinfo", "status": "+", "id": 900001}],
        [{"name": "review", "status": "?", "requestee": "watcher@example.com"}],
        [{"name": "needinfo", "status": "?"}],
        [
            {
                "name": "needinfo",
                "status": "?",
                "requestee": "watcher@example.com",
                "id": 900001,
            }
        ],
        [
            {
                "name": "needinfo",
                "status": "?",
                "requestee": "watcher@example.com",
                "type_id": 800,
            }
        ],
        [
            {
                "name": "needinfo",
                "status": "?",
                "requestee": "watcher@example.com",
                "new": True,
            }
        ],
        [
            {
                "name": "needinfo",
                "status": "?",
                "requestee": "watcher@example.com",
                "x": 1,
            }
        ],
        [],
        "watcher@example.com",
        ["watcher@example.com"],
    ):
        with pytest.raises(ToolError):
            _update(rec, {"flags": flags})
    assert len(rec.actions) == 1


def _stale_reporter_state(
    bug_new,
    comments,
    extra_requestee=None,
    setter=BOT_ACCOUNT,
    answered=False,
    now=LATER,
):
    # The reporter's open needinfo from the developer is gone in this scenario. Unless
    # ``answered``, the reporter has not commented since.
    bug = with_needinfo(dict(bug_new, flags=[]), "reporter@example.com", setter=setter)
    if extra_requestee:
        bug = with_needinfo(bug, extra_requestee, setter=setter)
    comments = comments if answered else comments[:3]
    return TriageState(bug=bug, comments=comments, now=now)


def test_incomplete_is_recorded_when_every_needinfo_is_on_the_reporter_and_stale(
    bug_new, comments_returning
):
    for setter in (BOT_ACCOUNT, "dev@example.org"):
        rec = _recorder(
            _stale_reporter_state(bug_new, comments_returning, setter=setter),
            close_verdicts=_verdicts(("INCOMPLETE", None)),
        )
        _comment(rec)
        _update(rec, {"status": "RESOLVED", "resolution": "INCOMPLETE"})
        assert len(rec.actions) == 2


def _answered_state(bug_new, comments):
    # The reporter answered on the 14th; on the 27th that is 13 quiet days.
    day_27 = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.timezone.utc)
    return _stale_reporter_state(bug_new, comments, answered=True, now=day_27)


@pytest.mark.parametrize(
    ("make_state", "reason"),
    [
        (_answered_state, "reporter"),
        (
            lambda bug, comments: _stale_reporter_state(
                bug, comments, setter="reporter@example.com"
            ),
            "INCOMPLETE",
        ),
        (
            lambda bug, comments: _stale_reporter_state(
                bug, comments, "dev@example.org"
            ),
            "INCOMPLETE is allowed only",
        ),
        (
            lambda bug, comments: TriageState(bug=bug, comments=comments, now=NOW),
            "INCOMPLETE",
        ),
    ],
    ids=[
        "reporter-answered",
        "self-set-reminder",
        "needinfo-on-another",
        "not-waiting",
    ],
)
def test_incomplete_is_refused_unless_the_reporter_is_quiet(
    bug_new, comments_returning, make_state, reason
):
    rec = _recorder(make_state(bug_new, comments_returning))
    _comment(rec)
    with pytest.raises(ToolError, match=reason):
        _update(rec, {"status": "RESOLVED", "resolution": "INCOMPLETE"})
    assert len(rec.actions) == 1


def test_every_other_closing_is_refused(bug_new, comments_returning):
    rec = _recorder(_stale_reporter_state(bug_new, comments_returning))
    _comment(rec)
    for changes in (
        {"status": "RESOLVED", "resolution": "WORKSFORME"},
        {"status": "RESOLVED", "resolution": "MOVED"},
        {"status": "RESOLVED"},
        {"resolution": "INCOMPLETE"},
        {"status": "NEW"},
        {"status": "VERIFIED", "resolution": "FIXED"},
    ):
        with pytest.raises(ToolError):
            _update(rec, changes)
    assert len(rec.actions) == 1


CLOSES = (
    ({"status": "RESOLVED", "resolution": "INVALID"}, ("INVALID", None)),
    (
        {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": DUPE_TARGET},
        ("DUPLICATE", DUPE_TARGET),
    ),
)


@pytest.mark.parametrize(("changes", "close"), CLOSES)
def test_a_close_with_no_verdict_is_refused_and_keeps_the_allowance(
    new_state, changes, close
):
    verdicts = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=verdicts)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, dict(changes))
    assert "Spawn close_verifier" in str(e.value)
    assert len(rec.actions) == 1
    verdicts.verdicts.append(CloseVerdict(*close, "confirm", "checked", ()))
    _update(rec, dict(changes))
    assert len(rec.actions) == 2


@pytest.mark.parametrize(("changes", "close"), CLOSES)
def test_a_close_after_a_refute_is_refused(new_state, changes, close):
    rec = _recorder(new_state, close_verdicts=_verdicts(close, verdict="refute"))
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, dict(changes))
    assert "refuted" in str(e.value) and "Analyzed or Needs info" in str(e.value)
    assert len(rec.actions) == 1


def test_a_confirm_for_another_resolution_does_not_close(new_state):
    rec = _recorder(new_state, close_verdicts=_verdicts(("WONTFIX", None)))
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, {"status": "RESOLVED", "resolution": "INVALID"})
    assert "RESOLVED INVALID" in str(e.value)
    assert len(rec.actions) == 1


def test_a_confirm_for_another_dupe_of_does_not_close(new_state):
    rec = _recorder(new_state, close_verdicts=_verdicts(("DUPLICATE", DUPE_TARGET)))
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _duplicate(rec, DUPE_TARGET + 1)
    assert f"dupe_of {DUPE_TARGET + 1}" in str(e.value)
    assert len(rec.actions) == 1


def test_a_confirm_after_the_comment_does_not_close(new_state):
    verdicts = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=verdicts)
    _comment(rec)
    assert verdicts.comment_recorded
    verdicts.verdicts.append(
        CloseVerdict("INVALID", None, "confirm", "checked", (), before_comment=False)
    )
    with pytest.raises(ToolError) as e:
        _update(rec, {"status": "RESOLVED", "resolution": "INVALID"})
    assert "Spawn close_verifier before you record the comment" in str(e.value)
    assert len(rec.actions) == 1


def test_every_refused_close_is_counted(new_state):
    verdicts = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=verdicts)
    _comment(rec)
    for changes in (
        {"status": "RESOLVED", "resolution": "INVALID"},
        {"status": "RESOLVED", "resolution": "WORKSFORME"},
    ):
        with pytest.raises(ToolError):
            _update(rec, changes)
    with pytest.raises(ToolError):
        _update(rec, {"severity": "S1"})
    assert len(verdicts.refused) == 2


def test_a_close_refused_by_any_check_is_counted(new_state):
    verdicts = _verdicts(("DUPLICATE", DUPE_TARGET), ("INVALID", None))
    rec = _recorder(new_state, close_verdicts=verdicts)
    _comment(rec)
    for changes in (
        {
            "status": "RESOLVED",
            "resolution": "DUPLICATE",
            "dupe_of": DUPE_TARGET,
            "blocks": {"add": [KNOWN_META]},
        },
        {"status": "RESOLVED", "resolution": "INVALID", "assigned_to": "a@example.com"},
        {"status": "RESOLVED", "resolution": "INVALID", "severity": "S1"},
        {"status": "RESOLVED", "resolution": "INVALID", "keywords": {"add": ["x"]}},
    ):
        with pytest.raises(ToolError):
            _update(rec, changes)
    assert len(verdicts.refused) == 4
    _update(rec, {"status": "RESOLVED", "resolution": "INVALID"})
    with pytest.raises(ToolError) as e:
        _update(rec, {"status": "RESOLVED", "resolution": "INVALID"})
    assert "already recorded" in str(e.value)
    assert len(verdicts.refused) == 5


def test_a_refused_change_on_another_bug_is_not_a_refused_close(new_state):
    verdicts = _verdicts(("DUPLICATE", DUPE_TARGET))
    rec = _recorder(new_state, close_verdicts=verdicts)
    _duplicate_of_target(rec)
    with pytest.raises(ToolError):
        _update(
            rec,
            {"status": "RESOLVED", "resolution": "INVALID"},
            bug_id=DUPE_TARGET,
        )
    assert verdicts.refused == []


def test_a_refused_close_keeps_its_resolution_and_dupe_of(new_state):
    verdicts = _verdicts(("DUPLICATE", DUPE_TARGET + 1))
    rec = _recorder(new_state, close_verdicts=verdicts)
    _comment(rec)
    with pytest.raises(ToolError):
        _duplicate(rec, DUPE_TARGET)
    _duplicate(rec, DUPE_TARGET + 1)
    assert verdicts.refused == [("DUPLICATE", DUPE_TARGET)]
    assert "refused 1 close(s)" in verdicts.warning(("DUPLICATE", DUPE_TARGET + 1))


@pytest.mark.parametrize("bug_id", [str(BUG), None])
def test_a_close_with_a_malformed_or_missing_bug_id_is_counted(new_state, bug_id):
    verdicts = _verdicts(("INVALID", None))
    rec = _recorder(new_state, close_verdicts=verdicts)
    _comment(rec)
    with pytest.raises(ToolError):
        rec.record(
            "bugzilla.update_bug",
            {
                "bug_id": bug_id,
                "changes": {"status": "RESOLVED", "resolution": "INVALID"},
            },
            reasoning="r",
        )
    assert verdicts.refused == [("INVALID", None)]
    assert len(rec.actions) == 1


def test_a_refused_comment_is_not_marked_as_recorded(new_state):
    verdicts = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=verdicts)
    with pytest.raises(ToolError):
        _comment(rec, is_private=True)
    assert not verdicts.comment_recorded


def test_a_close_with_a_matching_confirm_is_recorded(new_state):
    rec = _recorder(new_state, close_verdicts=_verdicts(("INVALID", None)))
    _comment(rec)
    _update(rec, {"status": "RESOLVED", "resolution": "INVALID"})
    assert len(rec.actions) == 2


def test_a_component_move_needs_no_verdict(new_state):
    rec = _recorder(new_state, close_verdicts=CloseVerdicts())
    _comment(rec)
    _update(rec, {"product": "Core", "component": "Graphics"})
    assert len(rec.actions) == 2


def test_the_no_team_run_needs_a_verdict_too(no_team, new_state):
    rec = _recorder(new_state, close_verdicts=CloseVerdicts())
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, {"status": "RESOLVED", "resolution": "INVALID"})
    assert "close_verifier" in str(e.value)
    assert len(rec.actions) == 1


def test_a_refused_field_change_leaves_the_run_free_to_retry(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    with pytest.raises(ToolError):
        _update(rec, {"severity": "S1"})
    _update(rec, {"severity": "S2"})
    assert [a["type"] for a in rec.actions] == [
        "bugzilla.add_comment",
        "bugzilla.update_bug",
    ]


def test_a_known_regressor_may_be_added(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    for changes in (
        {"regressed_by": {"add": [BUG]}},
        {"regressed_by": {"add": ["9000003"]}},
        {"regressed_by": {"remove": [9000003]}},
        {"regressed_by": [9000003]},
    ):
        with pytest.raises(ToolError):
            _update(rec, changes)
    _update(
        rec, {"regressed_by": {"add": [9000003]}, "keywords": {"add": ["regression"]}}
    )
    assert len(rec.actions) == 2


def test_a_regressor_already_on_the_bug_is_refused(bug_new, comments_new):
    state = TriageState(
        bug=dict(bug_new, regressed_by=[9000003]), comments=comments_new, now=NOW
    )
    rec = _recorder(state)
    _comment(rec)
    for item in (9000003, 9000005):
        with pytest.raises(ToolError, match="already regressed by 9000003"):
            _update(rec, {"regressed_by": {"add": [item, 9000003]}})
    assert len(rec.actions) == 1
    _update(rec, {"regressed_by": {"add": [9000005]}})
    assert len(rec.actions) == 2


def test_a_regressor_that_this_bug_regressed_is_refused(bug_new, comments_new):
    # Bugzilla refuses the loop (error 116), so the apply step would fail.
    state = TriageState(
        bug=dict(bug_new, regressions=[9000003]), comments=comments_new, now=NOW
    )
    rec = _recorder(state)
    _comment(rec)
    with pytest.raises(ToolError):
        _update(rec, {"regressed_by": {"add": [9000003]}})
    assert len(rec.actions) == 1


def test_a_related_bug_may_be_added_to_see_also(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    for changes in (
        {"see_also": {"add": [BUG]}},
        {"see_also": {"add": ["9000003"]}},
        {"see_also": {"add": ["https://bugzilla.mozilla.org/show_bug.cgi?id=9000003"]}},
        {"see_also": {"add": [True]}},
        {"see_also": {"add": []}},
        {"see_also": {"remove": [9000003]}},
        {"see_also": [9000003]},
    ):
        with pytest.raises(ToolError, match="see_also"):
            _update(rec, changes)
    assert len(rec.actions) == 1
    _update(rec, {"see_also": {"add": [9000003]}})
    assert rec.actions[-1]["params"]["changes"] == {"see_also": {"add": [9000003]}}


def test_a_see_also_bug_already_on_the_bug_is_refused(bug_new, comments_new):
    see_also = [
        "https://bugzilla.mozilla.org/show_bug.cgi?id=9000003",
        "https://github.com/example/project/issues/9000005",
    ]
    state = TriageState(
        bug=dict(bug_new, see_also=see_also), comments=comments_new, now=NOW
    )
    rec = _recorder(state)
    _comment(rec)
    with pytest.raises(ToolError, match="already has bug 9000003 in see_also"):
        _update(rec, {"see_also": {"add": [9000005, 9000003]}})
    assert len(rec.actions) == 1
    _update(rec, {"see_also": {"add": [9000005]}})
    assert len(rec.actions) == 2


def test_see_also_bug_ids_reads_only_bugzilla_bug_urls():
    assert hooks.see_also_bug_ids(
        [
            "https://bugzilla.mozilla.org/show_bug.cgi?id=9000003",
            "https://bugzilla.mozilla.org/show_bug.cgi?id=9000004#c2",
            "http://bugzilla.mozilla.org/show_bug.cgi?id=9000007",
            "HTTPS://Bugzilla.Mozilla.org/Show_Bug.cgi?id=9000008",
            "https://bugzilla.mozilla.org/show_bug.cgi?id=9000009&format=default",
            "https://bugzilla.example.com/show_bug.cgi?id=9000005",
            "https://github.com/example/project/issues/9000006",
            "https://bugzilla.mozilla.org/show_bug.cgi?id=abc",
            None,
        ]
    ) == {9000003, 9000004, 9000007, 9000008, 9000009}
    assert hooks.see_also_bug_ids(None) == set()


def test_a_needinfo_on_a_bot_alias_is_refused(bug_new, comments_new):
    alias = "triage-bot@example.com"
    state = TriageState(
        bug=dict(bug_new, cc=[*bug_new["cc"], alias]),
        comments=comments_new,
        now=NOW,
        bot_aliases=frozenset({alias}),
    )
    rec = _recorder(state)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, _ni(alias))
    assert "bot" in str(e.value)


# --- the comment after a refute -------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        "We recommend that a triager closes this bug as INVALID.",
        "This looks like a DUPLICATE of the older report.",
        "A triager can mark it WORKSFORME.",
        "We think this bug can be closed.",
        "Please Close this bug.",
        "We think this is invalid.",
        "It looks like a duplicate of the older report.",
        "A triager closed this bug once.",
        "We suggest closing this bug.",
        "A triager resolves this bug later.",
        "Please resolve this bug.",
        "Closing is up to the team.",
        "The code was fixed in a later change.",
    ],
)
def test_after_a_refute_a_comment_that_asks_for_a_close_is_refused(body):
    rec = _recorder(close_verdicts=_verdicts(("DUPLICATE", 9000002), verdict="refute"))
    with pytest.raises(ToolError, match="refuted the close"):
        _comment(rec, _text(body))
    assert rec.actions == []
    _comment(rec, _text("The cause is still open. We need a developer to look."))
    assert len(rec.actions) == 1


def test_after_a_refute_a_normal_analyzed_comment_is_recorded():
    rec = _recorder(close_verdicts=_verdicts(("INVALID", None), verdict="refute"))
    _comment(
        rec,
        _text(
            "The cause is in the WebM demuxer. A width of 2^31 trips a debug "
            "assertion. The bug stays open for a fix. The `INVALID` state in "
            "code is quoted. The next step is a bounds check in the parser."
        ),
    )
    assert len(rec.actions) == 1


def test_without_a_refute_a_closing_comment_is_recorded():
    rec = _recorder(close_verdicts=_verdicts(("INVALID", None)))
    _comment(
        rec, _text("This works as the spec says, so we close this bug as INVALID.")
    )
    assert len(rec.actions) == 1


# --- whiteboard fix tags ---------------------------------------------------- #

EASY = TERMS.whiteboard.easy_fix
GFB = TERMS.whiteboard.gfb_candidate


def _whiteboard_state(bug_new, comments_new, whiteboard):
    return TriageState(
        bug=dict(bug_new, whiteboard=whiteboard), comments=comments_new, now=NOW
    )


def _tagged(state, tags):
    rec = _recorder(state)
    _comment(rec)
    _update(rec, {"severity": "S3", "whiteboard": {"add": tags}})
    return rec.actions[-1]["params"]["changes"]["whiteboard"]


@pytest.mark.parametrize(
    ("current", "tags", "expected"),
    [
        ("", [EASY], EASY),
        ("", [EASY, GFB], f"{EASY} {GFB}"),
        ("   ", [EASY], f"   {EASY}"),
        ("", [GFB, EASY], f"{EASY} {GFB}"),
        ("[av1] [fuzzblocker]", [EASY], f"[av1] [fuzzblocker] {EASY}"),
        ("[av1] ", [EASY, GFB], f"[av1] {EASY} {GFB}"),
        (f"[av1] {EASY}", [GFB], f"[av1] {EASY} {GFB}"),
    ],
    ids=[
        "easy",
        "both",
        "keeps only spaces",
        "both in any order",
        "keeps text",
        "keeps space",
        "gfb",
    ],
)
def test_the_fix_tags_are_appended_to_the_whiteboard(
    bug_new, comments_new, current, tags, expected
):
    state = _whiteboard_state(bug_new, comments_new, current)
    assert _tagged(state, tags) == expected


@pytest.mark.parametrize(
    ("current", "whiteboard", "reason"),
    [
        ("[av1]", "", "may only be added to"),
        ("[av1]", f"[av1] {EASY}", "may only be added to"),
        ("[av1]", EASY, "may only be added to"),
        ("[av1]", {"add": []}, "non-empty list"),
        ("[av1]", {"remove": ["[av1]"]}, "may only be added to"),
        ("[av1]", {"add": [EASY], "remove": ["[av1]"]}, "may only be added to"),
        ("[av1]", {"add": ["[av1-new]"]}, "may only add"),
        ("[av1]", {"add": [TERMS.whiteboard.skip]}, "may only add"),
        ("[av1]", {"add": ["[gecko-triage:EASY-FIX] "]}, "may only add"),
        ("[av1]", {"add": [GFB]}, "needs"),
        ("[av1]", {"add": [EASY, EASY]}, "once"),
        (f"[av1]{EASY}", {"add": [EASY]}, "already has"),
        ("[Gecko-Triage:Easy-Fix]", {"add": [EASY]}, "already has"),
        (f"{EASY} {GFB}", {"add": [GFB]}, "already has"),
    ],
)
def test_every_other_whiteboard_change_is_refused(
    bug_new, comments_new, current, whiteboard, reason
):
    rec = _recorder(_whiteboard_state(bug_new, comments_new, current))
    _comment(rec)
    with pytest.raises(ToolError, match=reason):
        _update(rec, {"severity": "S3", "whiteboard": whiteboard})
    assert len(rec.actions) == 1
    _update(rec, {"severity": "S3"})
    assert rec.actions[-1]["params"]["changes"] == {"severity": "S3"}


@pytest.mark.parametrize("whiteboard", [None, 5, "missing"])
def test_a_fix_tag_is_refused_when_the_whiteboard_was_not_read(
    bug_new, comments_new, whiteboard
):
    bug = dict(bug_new, whiteboard=whiteboard)
    if whiteboard == "missing":
        del bug["whiteboard"]
    rec = _recorder(TriageState(bug=bug, comments=comments_new, now=NOW))
    _comment(rec)
    with pytest.raises(ToolError, match="was not read"):
        _update(rec, {"severity": "S3", "whiteboard": {"add": [EASY]}})
    assert len(rec.actions) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "RESOLVED", "resolution": "INVALID"},
        {"component": "Audio/Video: cubeb"},
        {"product": "Toolkit", "component": "General"},
    ],
    ids=["close", "move", "move to another product"],
)
def test_a_fix_tag_on_a_close_or_a_move_is_refused(new_state, changes):
    rec = _recorder(new_state, close_verdicts=_verdicts(("INVALID", None)))
    _comment(rec)
    with pytest.raises(ToolError, match="not analyzed"):
        _update(rec, {**changes, "whiteboard": {"add": [EASY]}})
    assert len(rec.actions) == 1
