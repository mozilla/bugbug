"""Tests for the plan the agent parses from its final message and the consent it derives.

`may_apply_unattended` decides whether a run's recorded actions reach a real bug with
nobody in between, so it is covered as closely as the hooks are.
"""

import json
from pathlib import Path

import pytest
from conftest import full_prompt
from hackbot_agents.gecko_triage.agent import (
    DECISIONS,
    MAX_ISSUE_TYPE_NOTE,
    GeckoTriageResult,
    Plan,
    analyzed_refusal,
    checked_issue_type,
    close_mismatch,
    issue_type_refusal,
    may_apply_unattended,
    parse_bug_id,
    parse_decision,
    parse_plan,
    render_components,
)
from hackbot_agents.gecko_triage.closing import CloseVerdict, CloseVerdicts
from hackbot_agents.gecko_triage.config import (
    CAPTURE_PLACEHOLDER,
    PEOPLE,
    TEAM,
    TRIAGE_SCOPE,
)

RULES = Path(__file__).parents[1] / "hackbot_agents" / "gecko_triage" / "rules"
COMMENT = {"type": "bugzilla.add_comment", "params": {"bug_id": 1, "text": "x"}}
SEVERITY = {
    "type": "bugzilla.update_bug",
    "params": {"bug_id": 1, "changes": {"severity": "S3"}},
}
INCOMPLETE = {
    "type": "bugzilla.update_bug",
    "params": {
        "bug_id": 1,
        "changes": {"status": "RESOLVED", "resolution": "INCOMPLETE"},
    },
}


def _block(body: str) -> str:
    return f"Done.\n\n```json\n{body}\n```"


def test_the_system_prompt_renders(new_state):
    # system.md goes through str.format, so a literal brace must be doubled or startup
    # raises KeyError and the run never begins.
    prompt = full_prompt(new_state)
    assert "{rules}" not in prompt and "{state}" not in prompt and "{bug}" not in prompt
    assert '<rule_file set="general" name="workflow.md">' in prompt
    assert "The bug has no open needinfo." in prompt or "open needinfo is on" in prompt
    assert "filed by a **reporter**" in prompt
    assert "`stalled`" in prompt and "`regression`" in prompt
    assert TEAM.comment.capture_block in prompt and CAPTURE_PLACEHOLDER in prompt
    assert '"decision": "needs_info' in prompt
    assert "`*.mozilla.org`" in prompt and "`searchfox.org`" in prompt
    assert '{"name": "needinfo", "status": "?", "requestee": "<email>"}' in prompt
    for entry in TRIAGE_SCOPE:
        assert f"**{entry.key}**" in prompt, entry.key


def test_the_link_hosts_cover_the_spec_and_bug_trackers():
    from hackbot_agents.gecko_triage.hooks import link_host_allowed

    for url in (
        "https://phabricator.services.mozilla.com/D1",
        "https://webaudio.github.io/web-audio-api/",
        "https://aomediacodec.github.io/av1-spec/",
        "https://www.itu.int/rec/T-REC-H.264",
        "https://rfc-editor.org/rfc/rfc6716",
        "https://www.rfc-editor.org/rfc/rfc6716",
        "https://crbug.com/1",
        "https://webkit.org/blog/",
        "https://bugs.webkit.org/show_bug.cgi?id=1",
        "https://bugs.chromium.org/p/chromium/issues/detail?id=1",
        "https://wpt.fyi/results/media",
        "https://mozilla.github.io/mozregression/",
        "https://firefox-source-docs.mozilla.org/",
        "https://developer.mozilla.org/",
    ):
        assert link_host_allowed(url), url


def test_no_link_host_is_covered_by_a_wildcard():
    from hackbot_agents.gecko_triage.config import ALLOWED_LINK_HOSTS

    wildcards = [h[1:] for h in ALLOWED_LINK_HOSTS if h.startswith("*.")]
    for host in ALLOWED_LINK_HOSTS:
        assert not any(host.endswith(w) for w in wildcards if f"*{w}" != host), host


def test_every_component_declares_a_tree_with_the_slash_convention():
    for entry in TRIAGE_SCOPE:
        assert entry.trees, entry.key
        for tree in (*entry.trees, *entry.doc_trees):
            # A directory ends with a slash; a file has an extension. `docs.docs_for`
            # keys off the slash.
            assert tree.endswith("/") or "." in tree.rsplit("/", 1)[-1], tree


def test_render_components_names_every_component():
    rendered = render_components(TRIAGE_SCOPE)
    for entry in TRIAGE_SCOPE:
        assert entry.key in rendered


def test_decisions_are_normalized():
    for raw in ("needs_info", "Needs-Info", " needs info ", "NEEDS_INFO"):
        assert parse_decision(raw) == "needs_info"
    assert parse_decision("nonsense") is None
    assert parse_decision(None) is None
    assert set(DECISIONS) >= {"needs_info", "analyzed", "incomplete", "watching"}


def test_the_old_triaged_decision_is_not_read():
    assert "triaged" not in DECISIONS
    assert parse_decision("triaged") is None
    assert parse_decision("Analyzed") == "analyzed"


def _comment(text):
    return {"type": "bugzilla.add_comment", "params": {"bug_id": 1, "text": text}}


def test_analyzed_with_nothing_asked_is_accepted():
    assert analyzed_refusal(Plan(decision="analyzed"), []) is None
    assert analyzed_refusal(Plan(decision="analyzed", asked=[]), []) is None
    assert analyzed_refusal(Plan(decision="analyzed"), [COMMENT]) is None


def test_analyzed_with_something_asked_in_the_plan_is_refused():
    reason = analyzed_refusal(Plan(decision="analyzed", asked=["profile"]), [])
    assert reason and "needs_info" in reason and "profile" in reason


def test_the_comment_text_no_longer_decides_analyzed():
    # The footer is gone: the plan's `asked` and the needinfos decide, not a line in
    # the comment that looks like the old footer.
    old_footer = "gecko-triage · rules 0.1.0 · asked: media log · unread: none"
    comment = _comment(f"Analysis.\n\n{old_footer}")
    assert analyzed_refusal(Plan(decision="analyzed"), [comment]) is None


def test_only_analyzed_is_bound_by_the_asked_list():
    asking = [_comment("Analysis.")]
    for decision in ("needs_info", "duplicate", "wrong_component", "watching", None):
        assert analyzed_refusal(Plan(decision=decision, asked=["x"]), asking) is None


def test_only_the_preflight_may_skip():
    assert parse_decision("skipped") is None
    assert parse_plan(_block('{"decision": "Skipped"}')).decision is None
    assert not may_apply_unattended(
        [COMMENT], parse_plan(_block('{"decision": "skipped"}'))
    )
    result = GeckoTriageResult(bug_id=1, num_turns=0, decision="skipped")
    assert result.decision == "skipped"


def test_bug_ids_are_normalized():
    assert parse_bug_id(1445470) == 1445470
    assert parse_bug_id("1445470") == 1445470
    assert parse_bug_id(True) is None
    assert parse_bug_id(0) is None
    assert parse_bug_id("meta") is None


def test_parse_plan_reads_the_last_json_block_and_degrades_per_field():
    text = _block(
        '{"product": "Core", "component": "Audio/Video: Playback", "decision": "Analyzed", '
        '"summary": "s", "asked": "media log", "unread": [], "severity": "s3", '
        '"priority": "P9", "needinfo": ["r@example.com"], "meta_bug": "1445470", '
        '"duplicate_of": "none"}'
    )
    plan = parse_plan(text)
    assert plan.decision == "analyzed"
    assert plan.asked == ["media log"]
    assert plan.unread == []
    assert plan.severity == "S3"
    assert plan.priority is None
    assert plan.meta_bug == 1445470
    assert plan.duplicate_of is None
    assert plan.component == "Audio/Video: Playback"


def test_parse_plan_fails_closed():
    assert parse_plan(None) == Plan()
    assert parse_plan("no block") == Plan()
    assert parse_plan(_block("not json")) == Plan()
    assert parse_plan(_block("[1, 2]")) == Plan()
    assert parse_plan(_block('{"decision": "nonsense", "extra": 1}')).decision is None


def test_a_run_consents_when_the_rules_pass():
    plan = Plan(decision="analyzed")
    assert may_apply_unattended([COMMENT, SEVERITY], plan)
    assert may_apply_unattended([COMMENT], Plan(decision="needs_info"))


def test_a_run_withholds_consent_without_a_comment_or_a_decision():
    assert not may_apply_unattended([], Plan(decision="analyzed"))
    assert not may_apply_unattended([SEVERITY], Plan(decision="analyzed"))
    assert not may_apply_unattended([COMMENT], Plan())
    assert not may_apply_unattended([COMMENT], Plan(decision="garbled"))


def _change(**changes):
    return {"type": "bugzilla.update_bug", "params": {"bug_id": 1, "changes": changes}}


def test_consent_does_not_depend_on_which_field_change_the_hooks_allowed():
    # The hooks bound the field changes, so no change other than a close adds or
    # withholds consent on its own: the comment and the decision decide.
    plan = Plan(decision="analyzed")
    for action in (
        _change(product="Core", component="Graphics"),
        _change(blocks={"add": [2]}),
        _change(keywords={"add": ["stalled"]}),
    ):
        assert may_apply_unattended([COMMENT, action], plan), action
        assert may_apply_unattended([action, COMMENT], plan), action
        assert not may_apply_unattended([action], plan), action
        assert not may_apply_unattended([action, action], plan), action
        assert not may_apply_unattended([COMMENT, action], Plan()), action


def test_analyzed_with_a_none_asked_list_is_accepted():
    for asked in (["none"], "none", "None", ["N/A"], [" none "]):
        assert analyzed_refusal(Plan(decision="analyzed", asked=asked), []) is None
        assert (
            parse_plan(
                _block(f'{{"decision": "analyzed", "asked": {json.dumps(asked)}}}')
            ).asked
            == []
        )


def _needinfo_on(email):
    return {
        "type": "bugzilla.update_bug",
        "params": {
            "bug_id": 1,
            "changes": {
                "flags": [{"name": "needinfo", "status": "?", "requestee": email}]
            },
        },
    }


def test_analyzed_with_a_needinfo_on_a_lay_reporter_is_refused(new_state):
    reporter = new_state.reporter
    reason = analyzed_refusal(
        Plan(decision="analyzed"), [COMMENT, _needinfo_on(reporter)], new_state
    )
    assert reason and "needs_info" in reason


def test_analyzed_with_a_hand_off_needinfo_is_accepted(new_state):
    actions = [COMMENT, _needinfo_on("dev@example.com")]
    assert analyzed_refusal(Plan(decision="analyzed"), actions, new_state) is None


def _reported_by(state, email, real_name="Some One", **fields):
    bug = dict(state.bug, creator=email, **fields)
    bug["creator_detail"] = dict(state.bug["creator_detail"], real_name=real_name)
    return type(state)(bug=bug, comments=state.comments, now=state.now)


def _analyzed_with_reporter_needinfo(state):
    actions = [COMMENT, _needinfo_on(state.reporter)]
    return analyzed_refusal(Plan(decision="analyzed"), actions, state)


STAFF = f"dev{PEOPLE.employee_domain}"


def test_analyzed_with_a_needinfo_on_a_qa_reporter_is_refused(new_state):
    by_name = _reported_by(new_state, STAFF, real_name="Dev Eloper, QA")
    assert "needs_info" in _analyzed_with_reporter_needinfo(by_name)


def test_analyzed_with_a_needinfo_on_a_listed_qa_account_is_refused(
    new_state, monkeypatch
):
    people = PEOPLE.model_copy(update={"qa_accounts": {STAFF}})
    monkeypatch.setattr("hackbot_agents.gecko_triage.preflight.PEOPLE", people)
    state = _reported_by(new_state, STAFF)
    assert "needs_info" in _analyzed_with_reporter_needinfo(state)


def test_analyzed_with_a_needinfo_on_a_staff_reporter_is_accepted(new_state):
    state = _reported_by(new_state, STAFF, real_name="Dev Eloper")
    assert _analyzed_with_reporter_needinfo(state) is None


def test_a_qa_whiteboard_does_not_make_a_staff_reporter_qa(new_state):
    state = _reported_by(
        new_state, STAFF, real_name="Dev Eloper", cf_qa_whiteboard="[qa-triaged]"
    )
    assert _analyzed_with_reporter_needinfo(state) is None


def test_analyzed_with_a_needinfo_on_a_listed_expert_reporter_is_accepted(
    new_state, monkeypatch
):
    from hackbot_agents.gecko_triage import state

    monkeypatch.setattr(state, "NEEDINFO_EXPERTS", frozenset({new_state.reporter}))
    assert _analyzed_with_reporter_needinfo(new_state) is None


def test_parse_plan_reads_the_issue_type_and_its_note():
    plan = parse_plan(
        _block('{"issue_type": " Other ", "issue_type_note": " Seek lag "}')
    )
    assert (plan.issue_type, plan.issue_type_note) == ("other", "Seek lag")
    plan = parse_plan(_block('{"issue_type": "", "issue_type_note": ""}'))
    assert (plan.issue_type, plan.issue_type_note) == (None, None)
    assert parse_plan(_block('{"issue_type": 3}')).issue_type is None


def test_a_note_on_a_listed_issue_type_is_dropped():
    plan = parse_plan(_block('{"issue_type": "drm", "issue_type_note": "Widevine"}'))
    assert (plan.issue_type, plan.issue_type_note) == ("drm", None)
    assert Plan(issue_type_note="x").issue_type_note is None


def test_the_result_carries_the_issue_type():
    plan = parse_plan(_block('{"issue_type": "other", "issue_type_note": "Seek lag"}'))
    result = GeckoTriageResult(bug_id=1, num_turns=0, **plan.model_dump())
    findings = result.model_dump()
    assert (findings["issue_type"], findings["issue_type_note"]) == (
        "other",
        "Seek lag",
    )


def test_a_listed_issue_type_or_other_with_a_note_stands():
    for value in (t.value for t in TEAM.issue_types):
        assert issue_type_refusal(Plan(issue_type=value)) is None, value
    assert issue_type_refusal(Plan(issue_type="other", issue_type_note="x")) is None


@pytest.mark.parametrize(
    ("issue_type", "note", "reason"),
    [
        (None, None, "no issue_type"),
        ("crash", None, "is not one of"),
        ("playback stall", None, "is not"),
        ("other", None, "issue_type_note"),
        ("other", "asked bob@example.com", "email"),
    ],
)
def test_a_bad_issue_type_is_refused(issue_type, note, reason):
    refusal = issue_type_refusal(Plan(issue_type=issue_type, issue_type_note=note))
    assert refusal and reason in refusal


@pytest.mark.parametrize(
    "note", [":fullscreen exit glitch", "Seek at 10:30 lags", "HLS: slow start"]
)
def test_a_note_with_a_colon_word_stands(note):
    assert issue_type_refusal(Plan(issue_type="other", issue_type_note=note)) is None


@pytest.mark.parametrize(
    "value",
    [
        "Playback failure",
        "playback_failure",
        " PLAYBACK-FAILURE ",
        "playback  _failure",
    ],
)
def test_an_issue_type_is_normalized(value):
    assert Plan(issue_type=value).issue_type == "playback-failure"


@pytest.mark.parametrize("value", ["null", "None", " n/a ", ""])
def test_an_empty_word_is_no_issue_type(value):
    plan = Plan(issue_type=value, issue_type_note=value)
    assert (plan.issue_type, plan.issue_type_note) == (None, None)
    assert Plan(issue_type="other", issue_type_note=value).issue_type_note is None


def test_a_long_note_is_cut_after_the_checks():
    plan = Plan(issue_type="other", issue_type_note="word " * 30)
    checked, reason = checked_issue_type(plan)
    assert reason is None
    assert len(checked.issue_type_note) <= MAX_ISSUE_TYPE_NOTE
    assert checked.issue_type_note.startswith("word word")


def test_an_email_past_the_cut_still_drops_the_note():
    note = "x" * (MAX_ISSUE_TYPE_NOTE - 8) + " bob@example.com"
    checked, reason = checked_issue_type(Plan(issue_type="other", issue_type_note=note))
    assert "email" in reason
    assert (checked.issue_type, checked.issue_type_note) == (None, None)


def test_the_warning_does_not_copy_the_value():
    _, reason = checked_issue_type(Plan(issue_type="ask bob@example.com please"))
    assert "bob@example.com" not in reason


@pytest.mark.parametrize("value", ["`playback-failure`", "playback-failure.", '"drm"'])
def test_quotes_and_a_period_are_stripped(value):
    assert Plan(issue_type=value).issue_type in {"playback-failure", "drm"}


def test_checked_issue_type_drops_a_refused_value():
    plan = Plan(decision="analyzed", issue_type="other", issue_type_note="x")
    assert checked_issue_type(plan) == (plan, None)
    bad = Plan(decision="analyzed", issue_type="crash")
    checked, reason = checked_issue_type(bad)
    assert "is not one of" in reason
    assert checked == Plan(decision="analyzed")


def test_a_team_without_issue_types_takes_no_value(monkeypatch, new_state):
    from hackbot_agents.gecko_triage import agent

    monkeypatch.setattr(agent, "TEAM", TEAM.model_copy(update={"issue_types": ()}))
    monkeypatch.setattr(agent, "ISSUE_TYPES", frozenset())
    assert issue_type_refusal(Plan()) is None
    assert "no team list" in issue_type_refusal(Plan(issue_type="drm"))
    flat = " ".join(full_prompt(new_state).split())
    assert "This run has no list of issue types." in flat
    assert '"issue_type": null, "issue_type_note": null' in flat
    assert "Record the bug's main symptom" not in flat


CLOSES = (
    (INCOMPLETE, "incomplete"),
    (_change(status="RESOLVED", resolution="DUPLICATE", dupe_of=2), "duplicate"),
    (_change(status="RESOLVED", resolution="INVALID"), "invalid"),
)


@pytest.mark.parametrize(("action", "decision"), CLOSES)
def test_a_close_vouches_only_with_a_confirm_for_it(action, decision):
    plan = Plan(decision=decision)
    assert may_apply_unattended([COMMENT, action], plan, "confirmed")
    assert not may_apply_unattended([COMMENT, action], plan, "confirmed", "w")
    assert not may_apply_unattended([COMMENT, action], plan, "refuted")
    assert not may_apply_unattended([COMMENT, action], plan, None)
    assert not may_apply_unattended([COMMENT, action], plan)


@pytest.mark.parametrize("decision", ["invalid", "duplicate", "incomplete"])
def test_a_closing_decision_without_a_recorded_close_never_vouches(decision):
    plan = Plan(decision=decision)
    assert not may_apply_unattended([COMMENT], plan, "confirmed")
    assert not may_apply_unattended([COMMENT, SEVERITY], plan, None)
    assert close_mismatch(plan, [COMMENT]) == (
        f"the decision is {decision} but the run recorded no close"
    )


def test_a_close_under_another_decision_needs_the_confirm_too():
    action = _change(status="RESOLVED", resolution="FIXED")
    plan = Plan(decision="analyzed")
    assert not may_apply_unattended([COMMENT, action], plan)
    assert may_apply_unattended([COMMENT, action], plan, "confirmed")
    assert "RESOLVED FIXED but the decision is analyzed" in close_mismatch(
        plan, [COMMENT, action]
    )


@pytest.mark.parametrize(("action", "decision"), CLOSES)
def test_a_matching_close_and_decision_carry_no_warning(action, decision):
    plan = Plan(decision=decision, duplicate_of=2 if decision == "duplicate" else None)
    assert close_mismatch(plan, [COMMENT, action]) is None
    assert close_mismatch(plan, [COMMENT, action], CloseVerdicts()) is None
    assert close_mismatch(Plan(decision="needs_info"), [COMMENT, SEVERITY]) is None


INVALID = _change(status="RESOLVED", resolution="INVALID")
DUPLICATE = _change(status="RESOLVED", resolution="DUPLICATE", dupe_of=2)


@pytest.mark.parametrize(
    ("plan", "action", "warning"),
    [
        (Plan(decision="duplicate", duplicate_of=2), INVALID, "RESOLVED INVALID"),
        (Plan(decision="invalid"), DUPLICATE, "RESOLVED DUPLICATE"),
        (Plan(decision="incomplete"), INVALID, "decision is incomplete"),
        (Plan(decision="invalid"), INCOMPLETE, "RESOLVED INCOMPLETE"),
        (Plan(decision="duplicate", duplicate_of=3), DUPLICATE, "duplicate_of is 3"),
        (Plan(decision="duplicate"), DUPLICATE, "duplicate_of is None"),
        (
            Plan(decision="invalid"),
            _change(status="RESOLVED", resolution="FIXED"),
            "RESOLVED FIXED",
        ),
    ],
)
def test_the_decision_must_match_the_recorded_resolution(plan, action, warning):
    found = close_mismatch(plan, [COMMENT, action])
    assert warning in found
    assert not may_apply_unattended([COMMENT, action], plan, "confirmed", found)


def test_wontfix_goes_with_the_invalid_decision():
    action = _change(status="RESOLVED", resolution="WONTFIX")
    assert close_mismatch(Plan(decision="invalid"), [COMMENT, action]) is None


def test_a_refused_close_or_a_late_verifier_warns_and_withholds_consent():
    plan = Plan(decision="analyzed")
    for store in (
        CloseVerdicts(refused=[("INVALID", None)]),
        CloseVerdicts(late_spawns=1),
        CloseVerdicts(
            [CloseVerdict("INVALID", None, "refute", "r", (), before_comment=False)]
        ),
    ):
        warning = close_mismatch(plan, [COMMENT], store)
        assert warning is not None, store
        assert not may_apply_unattended([COMMENT], plan, None, warning)
    assert close_mismatch(plan, [COMMENT], CloseVerdicts()) is None
