"""Tests for the Slack notification an auto-applied run records.

Pure functions, no network: nothing is posted from the run, so there is nothing to
mock. What matters is that only an auto-applied run in a component with a channel
records anything at all, since the alternative is posting into a channel that did not
ask for it.
"""

import re

import pytest
from hackbot_agents.frontend_triage.agent import (
    FrontendTriageResult,
    SeverityAssessment,
)
from hackbot_agents.frontend_triage.config import TRIAGE_SCOPE
from hackbot_agents.frontend_triage.notify import (
    build_blocks,
    build_message,
    channel_for,
    record_notification,
)
from hackbot_runtime.actions import ActionsRecorder

BUG_ID = 1968342
RUN_ID = "1218e630-78c8"
BUG_LINK = f"<https://bugzilla.mozilla.org/show_bug.cgi?id={BUG_ID}|Bug {BUG_ID}>"
RUN_LINK = f"<https://hackbot.moz.tools/runs/{RUN_ID}|frontend-triage run details>"
SUMMARY = "Weather widget shows the previous city after changing location"


def _result(**overrides) -> FrontendTriageResult:
    fields = {
        "bug_id": BUG_ID,
        "num_turns": 14,
        "product": "Firefox",
        "component": "New Tab Page",
        "summary": SUMMARY,
        "confidence": "high",
        "actionable": True,
        "auto_apply": True,
        "severity_assessment": SeverityAssessment(
            suggested="S3", confidence="high", rationale="visible but recoverable"
        ),
    }
    return FrontendTriageResult(**{**fields, **overrides})


def _message(**overrides) -> str:
    return build_message(_result(**overrides), run_id=RUN_ID)


def test_reports_the_bug_and_the_run_in_two_lines():
    assert _message().splitlines() == [
        f"*{BUG_LINK} — {SUMMARY}*",
        RUN_LINK,
    ]


def test_an_s1_is_marked_and_named():
    # The level is spelled out next to the emoji so it still reads as an S1 where the
    # emoji doesn't render.
    line = _message(
        severity_assessment=SeverityAssessment(suggested="S1")
    ).splitlines()[0]
    assert line == f":red_circle: *{BUG_LINK} — {SUMMARY}* (S1)"


def test_every_other_severity_is_unmarked():
    for suggested in ("S2", "S3", "S4", None, "", "  "):
        line = _message(
            severity_assessment=SeverityAssessment(suggested=suggested)
        ).splitlines()[0]
        assert line == f"*{BUG_LINK} — {SUMMARY}*", suggested
    # A run that could not assess severity reports none at all.
    line = _message(severity_assessment=None).splitlines()[0]
    assert line == f"*{BUG_LINK} — {SUMMARY}*"


def test_a_lowercase_s1_is_still_an_s1():
    # `suggested` is read out of the agent's free-form JSON block, so "s1" must not
    # quietly read as an ordinary severity.
    line = _message(
        severity_assessment=SeverityAssessment(suggested=" s1 ")
    ).splitlines()[0]
    assert line.startswith(":red_circle: ")


def test_a_missing_summary_leaves_the_bug_link_alone():
    for summary in (None, "", "   "):
        assert _message(summary=summary).splitlines() == [f"*{BUG_LINK}*", RUN_LINK]


def test_the_channel_belongs_to_the_component():
    # Derived from the registry rather than one assert per component, so adding a
    # component is one entry in config.py and no test edit. The invariants a loop
    # cannot express are asserted concretely in the tests below.
    for entry in TRIAGE_SCOPE:
        assert channel_for(entry.product, entry.component) == entry.channel


def test_whitespace_around_either_half_is_stripped():
    # Surrounding whitespace is the agent's, not Bugzilla's.
    assert channel_for(" Firefox ", " New Tab Page ") == "#hnt-dev-triage"


def test_the_registry_names_each_component_once():
    # A duplicate key collapses silently in the derived `SLACK_CHANNELS`, so the second
    # entry's channel would win with nothing to show it had. That is the failure mode a
    # registry has as it grows, and it is invisible in a diff that only adds a line.
    keys = [entry.key for entry in TRIAGE_SCOPE]
    assert len(keys) == len(set(keys))


def test_every_channel_is_a_channel_name():
    # Slack rejects an unknown channel at apply time, long after the Bugzilla comment
    # and severity change have landed, and the run page is the only place it shows. A
    # missing `#` or a stray capital is the whole cost of that, so catch it here.
    for entry in TRIAGE_SCOPE:
        assert entry.channel.startswith("#"), entry.key
        assert entry.channel == entry.channel.strip().lower(), entry.key
        assert " " not in entry.channel, entry.key


def test_the_installer_and_the_updater_share_a_channel():
    # One team triages both, and a key that is off by a character notifies nobody
    # rather than failing, so each one is asserted rather than assumed from the other.
    triage = "#installer-updater-bug-triage"
    assert channel_for("Toolkit", "Application Update") == triage
    assert channel_for("Firefox", "Installer") == triage


def test_an_unowned_component_has_no_channel():
    # Fails closed rather than falling back to a default: another team's channel is a
    # worse outcome than silence.
    #
    # `Address Bar` is a component this agent will triage if handed one -- scoping.md
    # puts any user-facing Firefox defect in scope -- but it is not in `TRIAGE_SCOPE`,
    # so no team is told. That is the case worth pinning: in scope and unrouted are
    # different questions, and only the second one is this function's business.
    assert channel_for("Firefox", "Address Bar") is None
    assert channel_for("Core", "New Tab Page") is None
    # A component name is only owned within its own product: `History` routes to
    # #android-core-dev under Firefox for Android and nowhere at all under Firefox --
    # which has no `History` component in the first place, only `Bookmarks & History`.
    assert channel_for("Firefox", "History") is None
    assert channel_for("Firefox", None) is None
    assert channel_for(None, "New Tab Page") is None
    assert channel_for("", "") is None


def test_an_auto_applied_run_records_one_slack_action():
    recorder = ActionsRecorder()
    action = record_notification(recorder, _result(), run_id=RUN_ID)

    assert action is not None
    assert [a["type"] for a in recorder.actions] == ["slack.post_message"]
    assert action["params"]["channel"] == "#hnt-dev-triage"
    assert action["params"]["text"] == build_message(_result(), run_id=RUN_ID)
    # The layout travels with the text, which stays the fallback.
    assert action["params"]["blocks"] == build_blocks(_result(), run_id=RUN_ID)


# --- the Block Kit layout ---


def _blocks(**overrides) -> list[dict]:
    return build_blocks(_result(**overrides), run_id=RUN_ID)


def _block_of(kind: str, **overrides) -> dict | None:
    return next((b for b in _blocks(**overrides) if b["type"] == kind), None)


def test_the_layout_reads_bug_then_facts_then_run():
    assert [b["type"] for b in _blocks()] == ["section", "section", "context"]


def test_the_headline_links_the_bug_and_puts_the_summary_under_it():
    assert _blocks()[0] == {
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*{BUG_LINK}*\n{SUMMARY}"},
    }


def test_a_bug_with_no_summary_is_just_the_link():
    for summary in (None, "", "   "):
        assert _blocks(summary=summary)[0]["text"]["text"] == f"*{BUG_LINK}*"


def test_an_s1_leads_the_headline():
    # Marked and named, so it still reads as an S1 where the emoji does not render.
    headline = _blocks(
        severity_assessment=SeverityAssessment(suggested="S1", confidence="high")
    )[0]["text"]["text"]
    assert headline == f":red_circle: *S1* *{BUG_LINK}*\n{SUMMARY}"


def test_the_fields_grid_carries_the_severity_and_the_component():
    assert _blocks()[1]["fields"] == [
        {"type": "mrkdwn", "text": "*Severity*\nS3"},
        {"type": "mrkdwn", "text": "*Component*\nFirefox :: New Tab Page"},
    ]


def test_a_severity_the_bug_did_not_receive_says_so():
    # Below `high` the assessment is held back from the bug, so reporting it plainly
    # would read as a field that was written when it was not.
    fields = _blocks(
        severity_assessment=SeverityAssessment(suggested="S2", confidence="medium")
    )[1]["fields"]
    assert fields[0]["text"] == "*Severity*\nS2 (suggested, medium confidence)"


def test_a_field_with_nothing_to_say_is_dropped():
    fields = _blocks(severity_assessment=None)[1]["fields"]
    assert [f["text"] for f in fields] == ["*Component*\nFirefox :: New Tab Page"]
    # And with neither, the grid itself goes rather than rendering empty.
    assert [b["type"] for b in _blocks(severity_assessment=None, product=None)] == [
        "section",
        "context",
    ]


def test_the_run_sits_in_the_context_line():
    element = _block_of("context")["elements"][0]
    assert element["text"] == (
        "Triaged by frontend-triage · "
        f"<https://hackbot.moz.tools/runs/{RUN_ID}|run details>"
    )


def test_the_notification_asks_for_nothing_yet():
    # The layout is the whole change: no interactive element is posted until the
    # buttons land, so nothing here can be clicked.
    assert _block_of("actions") is None


# --- the two renderings say the same things ---

_LINK = re.compile(r"<([^|>]+)\|([^>]+)>")
# Punctuation that only ever joins words: dropped so that "(S1)" and "*S1*" are the
# same fact said twice, rather than two tokens that happen to look alike.
_PUNCTUATION = "*()·,—"


def _visible_text(blocks: list[dict]) -> str:
    """Every string a reader sees in `blocks`, whatever block it sits in."""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block.get("text"), dict):
            parts.append(block["text"]["text"])
        for field in block.get("fields", []):
            parts.append(field["text"])
        for element in block.get("elements", []):
            if isinstance(element.get("text"), dict):
                parts.append(element["text"]["text"])
            elif isinstance(element.get("text"), str):
                parts.append(element["text"])
    return "\n".join(parts)


def _facts(text: str) -> set[str]:
    """What `text` tells a reader, as words, with link markup reduced to its label."""
    labelled = _LINK.sub(lambda match: match.group(2), text)
    return {word.strip(_PUNCTUATION) for word in labelled.split()} - {""}


def _urls(text: str) -> set[str]:
    return {match.group(1) for match in _LINK.finditer(text)}


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"summary": None},
        {"severity_assessment": None},
        {"severity_assessment": SeverityAssessment(suggested="S1", confidence="high")},
        {
            "severity_assessment": SeverityAssessment(
                suggested="S1", confidence="medium"
            )
        },
        {"product": None, "component": None},
    ],
    ids=[
        "ordinary",
        "no-summary",
        "no-severity",
        "urgent",
        "urgent-suggested",
        "no-component",
    ],
)
def test_the_blocks_say_everything_the_fallback_text_says(overrides):
    # The blocks are what almost everyone reads, and the text is what Slack falls back
    # to. They may lay the same facts out differently, but nothing may be in the
    # fallback and missing from the blocks: that would be a fact only the people
    # reading a push notification ever see.
    result = _result(**overrides)
    text = build_message(result, run_id=RUN_ID)
    blocks = _visible_text(build_blocks(result, run_id=RUN_ID))

    assert _facts(text) <= _facts(blocks)
    # Labels may be worded differently; the things they link to may not.
    assert _urls(text) <= _urls(blocks)


def test_a_run_that_was_not_auto_applied_reports_nothing():
    # Medium and low results wrote nothing to the bug, so there is nothing to report.
    recorder = ActionsRecorder()
    assert (
        record_notification(
            recorder, _result(auto_apply=False, confidence="medium"), run_id=RUN_ID
        )
        is None
    )
    assert recorder.actions == []


def test_a_run_in_an_unowned_component_reports_nothing():
    recorder = ActionsRecorder()
    assert (
        record_notification(recorder, _result(component="Address Bar"), run_id=RUN_ID)
        is None
    )
    assert record_notification(recorder, _result(component=None), run_id=RUN_ID) is None
    assert recorder.actions == []
