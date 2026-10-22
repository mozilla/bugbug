"""Tests for a run with no team: the shared rules only, in the bug's own component."""

import os
import re
import subprocess
import sys

import pytest
from agent_tools.registry import ToolError
from conftest import (
    BUG,
    DUPE,
    DUPE_TARGET,
    KNOWN_META,
    LATER,
    NO_TEAM,
    NOW,
    _actions,
    _comment,
    _dupe_recorder,
    _duplicate_of_target,
    _fake_fetch,
    _record_verdict,
    _recorder,
    _result,
    _run,
    _target,
    _text,
    _update,
    full_prompt,
    with_needinfo,
)
from hackbot_agents.gecko_triage import agent, dupes, hooks, preflight
from hackbot_agents.gecko_triage.agent import (
    RULES_DIR,
    Plan,
    issue_type_refusal,
    make_duplicate_hunter,
    make_profile_analyst,
    may_apply_unattended,
    render_needinfo_experts,
)
from hackbot_agents.gecko_triage.config import (
    CAPTURE_PLACEHOLDER,
    CROSS_PRODUCT_DEFAULTS,
    TEAM,
    TEAMS_DIR,
    TERMS,
)
from hackbot_agents.gecko_triage.preflight import skip_reason
from hackbot_agents.gecko_triage.state import TriageState
from hackbot_agents.gecko_triage.targets import DupeTargetIsMeta, screen_writes
from hackbot_agents.gecko_triage.teamconfig import Component, scope_for
from hackbot_runtime.actions import ActionsRecorder

OTHER = {"product": "Core", "component": "Graphics: Layers"}
COMMENT = {"type": "bugzilla.add_comment", "params": {}}


@pytest.fixture
def other_bug(bug_new) -> dict:
    return dict(bug_new, **OTHER)


@pytest.fixture
def other_state(other_bug, comments_new) -> TriageState:
    return TriageState(bug=other_bug, comments=comments_new, now=NOW)


def _import_config(team: str | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "TEAM"}
    if team is not None:
        env["TEAM"] = team
    code = (
        "from hackbot_agents.gecko_triage import config as c; "
        f"print(repr({{n: getattr(c, n) for n in {sorted(NO_TEAM)!r}}}))"
    )
    return subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )


@pytest.mark.parametrize("team", [None, ""])
def test_the_config_imports_with_no_team(team):
    run = _import_config(team)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == repr(dict(sorted(NO_TEAM.items())))


@pytest.mark.parametrize("team", ["nosuch", "../media", "Media", " ", " media"])
def test_an_unknown_team_still_fails_the_import(team):
    run = _import_config(team)
    assert run.returncode != 0
    assert "ValueError" in run.stderr and "team" in run.stderr


def test_the_scope_is_the_bugs_own_component(other_bug):
    (own,) = scope_for(None, other_bug)
    assert (own.product, own.component) == (OTHER["product"], OTHER["component"])
    assert own.trees == ()
    assert scope_for(TEAM, other_bug) == TEAM.components


def test_the_preflight_triages_any_component(no_team, other_state):
    assert skip_reason(other_state) is None


def test_the_preflight_still_skips_a_meta_bug(no_team, other_bug):
    meta = dict(other_bug, keywords=["meta"])
    assert skip_reason(TriageState(bug=meta, now=NOW)) == "meta bug"


def test_the_duplicate_search_covers_the_bugs_own_component(no_team, other_bug):
    assert dupes.search_scope(other_bug) == ([OTHER["product"]], [OTHER["component"]])


def test_the_duplicate_search_covers_the_team_components(bug_new):
    products, components = dupes.search_scope(bug_new)
    assert components == [c.component for c in TEAM.components]
    assert products == sorted({c.product for c in TEAM.components})


def test_the_duplicate_hunter_searches_the_bugs_own_component(other_bug):
    prompt = make_duplicate_hunter(scope_for(None, other_bug)).prompt
    assert f"product `Core`: {OTHER['component']}" in prompt
    for entry in TEAM.components:
        assert entry.component not in prompt


def test_blocks_is_refused_and_the_refusal_keeps_the_allowance(no_team, other_state):
    rec = _recorder(other_state)
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, {"blocks": {"add": [KNOWN_META]}})
    assert "no team" in str(e.value)
    assert len(rec.actions) == 1
    _update(rec, {"severity": "S3"})
    assert len(rec.actions) == 2


def test_the_duplicate_target_meta_write_is_refused(no_team, other_state):
    rec = _dupe_recorder(other_state)
    _duplicate_of_target(rec)
    with pytest.raises(ToolError) as e:
        _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    assert "no team" in str(e.value)
    assert [a["params"]["bug_id"] for a in rec.actions] == [BUG, BUG]


def test_the_post_run_check_drops_a_meta_write_on_the_dupe_target(no_team):
    dropped = screen_writes(_actions(), BUG, {DUPE: _target(**OTHER)})
    assert [d.action_id for d in dropped] == ["m"]


def test_the_capture_placeholder_is_refused_and_keeps_the_allowance(no_team):
    rec = _recorder()
    with pytest.raises(ToolError) as e:
        _comment(rec, text=_text(f"Please help.\n\n{CAPTURE_PLACEHOLDER}"))
    assert "no capture block" in str(e.value)
    assert rec.actions == []
    _comment(rec)
    assert len(rec.actions) == 1


def test_the_capture_hook_passes_with_no_team(no_team):
    # With a team, this names the marker in a request and retypes two labels.
    text = _text(
        f"Please capture a profile with {TEAM.comment.capture_marker}. Use "
        f"{TEAM.comment.capture_labels[0]} and {TEAM.comment.capture_labels[1]}."
    )
    hooks.capture_block_hook({"params": {"text": text}})
    rec = _recorder()
    _comment(rec, text=text)
    assert len(rec.actions) == 1


def test_there_are_no_needinfo_experts(no_team, other_state):
    assert render_needinfo_experts() == "(none)"
    expert = TEAM.needinfo_experts[0].email
    rec = _recorder(other_state)
    _comment(rec)
    with pytest.raises(ToolError):
        _update(
            rec, {"flags": [{"name": "needinfo", "status": "?", "requestee": expert}]}
        )
    assert len(rec.actions) == 1


def test_the_prompt_names_no_team_rules_and_no_meta_bug(no_team, other_state):
    prompt = full_prompt(other_state)
    flat = " ".join(prompt.split())
    for path in RULES_DIR.glob("*.md"):
        if path.name != "README.md":
            assert f'<rule_file set="general" name="{path.name}">' in prompt
    assert 'set="team"' not in prompt
    assert str(TEAMS_DIR.resolve()) not in prompt
    assert "the team rules in" not in prompt
    assert "This run has no team" in flat
    assert "no meta bug applies" in flat
    # The general rules name the placeholder; only a team's parts may ask for it.
    parts = re.sub(r"<rule_file .*?</rule_file>", "", prompt, flags=re.DOTALL)
    assert CAPTURE_PLACEHOLDER not in parts
    assert TEAM.comment.capture_block not in prompt
    assert TEAM.label not in prompt
    assert f"**{OTHER['product']} :: {OTHER['component']}**" in prompt
    for entry in TEAM.components:
        assert f"**{entry.key}**" not in prompt
    for expert in TEAM.needinfo_experts:
        assert expert.email not in prompt


def test_the_team_prompt_keeps_its_team_parts(new_state):
    prompt = full_prompt(new_state)
    flat = " ".join(prompt.split())
    assert "This run has no team" not in flat
    assert CAPTURE_PLACEHOLDER in prompt
    assert TEAM.comment.capture_block in prompt
    assert TEAM.label in prompt
    assert "only meta bugs from the team's `meta-bugs.md`" in flat


def test_a_run_with_no_team_never_vouches(no_team):
    assert agent.TEAM is None
    for decision in ("analyzed", "needs_info", "duplicate"):
        assert not may_apply_unattended([COMMENT], Plan(decision=decision))


def test_a_run_with_a_team_still_vouches():
    assert may_apply_unattended([COMMENT], Plan(decision="analyzed"))


def test_the_prompt_says_what_to_do_with_no_team(no_team, other_state):
    flat = " ".join(full_prompt(other_state).split())
    assert "This run has no team rules. Skip every step that reads one." in flat
    assert "you can still move the bug to the component that owns the code" in flat
    for name in ("workflow.md", "diagnostics.md", "comment.md", "severity-priority.md"):
        assert "With no team" not in (RULES_DIR / name).read_text(), name


def test_a_bug_with_no_product_or_component_has_no_scope(no_team, other_bug):
    for empty in ({"product": ""}, {"component": ""}, {"component": None}):
        bug = dict(other_bug, **empty)
        assert scope_for(None, bug) == ()
        assert "out of scope" in skip_reason(TriageState(bug=bug, now=NOW))


def test_the_no_team_products_are_mozilla_central_products():
    products = TERMS.no_team.products
    assert {"Core", "Firefox", "Toolkit", "GeckoView"} <= set(products)
    assert len(products) == len(set(products))


@pytest.mark.parametrize(
    "product",
    [
        "Thunderbird",
        "bugzilla.mozilla.org",
        "Invalid Bugs",
        "Core Graveyard",
        "Websites",
    ],
)
def test_a_no_team_run_skips_a_product_outside_the_list(no_team, other_bug, product):
    bug = dict(other_bug, product=product, component="General")
    assert skip_reason(TriageState(bug=bug, now=NOW)) == (
        f"product {product!r} is outside the no-team products"
    )


@pytest.mark.parametrize("product", ["Firefox", "Toolkit", "Firefox for Android"])
def test_a_no_team_run_triages_a_listed_product(no_team, other_bug, product):
    bug = dict(other_bug, product=product, component="General")
    assert skip_reason(TriageState(bug=bug, now=NOW)) is None


def test_a_team_run_skips_another_product_by_its_scope(other_bug):
    bug = dict(other_bug, product="Thunderbird", component="General")
    assert "out of scope" in skip_reason(TriageState(bug=bug, now=NOW))


@pytest.mark.parametrize(
    ("fields", "restricted", "why"),
    [
        ({}, True, "restricted bug (withheld"),
        ({"groups": ["core-security"]}, False, "restricted bug (non-empty groups)"),
        ({"keywords": ["sec-high"]}, False, "security bug"),
        (
            {"status": "RESOLVED", "resolution": "FIXED"},
            False,
            "already RESOLVED FIXED",
        ),
    ],
)
def test_a_no_team_run_still_skips_on_another_component(
    no_team, other_bug, fields, restricted, why
):
    bug = dict(other_bug, **fields)
    reason = skip_reason(TriageState(bug=bug, now=NOW, restricted=restricted))
    assert reason.startswith(why)


def test_an_expert_reporter_may_get_incomplete_with_no_team(
    request, bug_new, comments_returning
):
    # An expert outside the staff domain, so only the expert list makes them one.
    expert = next(
        e.email for e in TEAM.needinfo_experts if not e.email.endswith("@mozilla.com")
    )
    bug = with_needinfo(dict(bug_new, flags=[], creator=expert), expert)
    comments = comments_returning[:3]
    assert not TriageState(bug=bug, comments=comments, now=LATER).incomplete_allowed
    request.getfixturevalue("no_team")
    state = TriageState(bug=bug, comments=comments, now=LATER)
    assert not state.reporter_is_developer_or_expert
    assert state.incomplete_allowed


def test_a_no_team_run_may_move_the_bug(no_team, other_state):
    rec = _recorder(other_state)
    _comment(rec)
    same = _update(rec, {"component": "Graphics: WebRender"})
    assert same["params"]["changes"] == {"component": "Graphics: WebRender"}

    rec = _recorder(other_state)
    _comment(rec)
    across = _update(rec, {"product": "Toolkit", "component": "General"})
    assert across["params"]["changes"] == {
        "product": "Toolkit",
        "component": "General",
        **CROSS_PRODUCT_DEFAULTS,
    }


def test_a_duplicate_of_a_meta_bug_is_recorded_then_fails_the_run(no_team, other_state):
    # With no team the hooks know no meta bug ids, so only the post-run check sees it.
    rec = _dupe_recorder(other_state)
    _duplicate_of_target(rec)
    assert rec.actions[1]["params"]["changes"]["dupe_of"] == DUPE_TARGET
    meta = {"id": DUPE_TARGET, "groups": [], "keywords": ["meta"], **OTHER}
    with pytest.raises(DupeTargetIsMeta):
        screen_writes(rec.list_actions(), BUG, {DUPE_TARGET: meta})


def test_the_prompt_has_no_team_experts_or_capture_rule(no_team, other_state):
    flat = " ".join(full_prompt(other_state).split())
    assert "This run has no needinfo experts." in flat
    assert "The team lists these developers" not in flat
    assert "for the team's area, as the team's `diagnostics.md`" not in flat
    analyst = make_profile_analyst()
    assert "team" not in analyst.prompt + analyst.description


def test_the_team_prompt_keeps_its_experts_and_capture_rule(new_state):
    flat = " ".join(full_prompt(new_state).split())
    assert "The team lists these developers by area" in flat
    assert "the team's area" in flat
    assert "the team's capture rule" in make_profile_analyst().description


def test_a_no_team_run_has_no_issue_type(no_team, other_state):
    assert issue_type_refusal(Plan(issue_type=None)) is None
    for value in ("drm", "other"):
        refusal = issue_type_refusal(Plan(issue_type=value, issue_type_note="x"))
        assert refusal and "no team list" in refusal, value
    flat = " ".join(full_prompt(other_state).split())
    assert "This run has no list of issue types." in flat
    assert '"issue_type": null, "issue_type_note": null' in flat
    assert "`playback-failure`" not in flat


async def test_a_no_team_run_drops_an_issue_type(
    no_team, fake_client, tmp_path, other_state
):
    fake_client.result = _result(
        '```json\n{"decision": "analyzed", "issue_type": "drm"}\n```'
    )
    recorder = ActionsRecorder()
    fake_client.script = lambda: recorder.record(
        "bugzilla.add_comment",
        {"bug_id": BUG, "text": "Short note.", "is_private": False},
        reasoning="r",
    )
    result = await _run(tmp_path, other_state, recorder)
    assert result.decision == "analyzed" and result.issue_type is None
    assert "no team list" in result.issue_type_warning


async def test_a_no_team_run_end_to_end(no_team, fake_client, tmp_path, other_state):
    recorder = ActionsRecorder()

    def script():
        recorder.record(
            "bugzilla.add_comment",
            {"bug_id": BUG, "text": "Short note.", "is_private": False},
            reasoning="r",
        )

    fake_client.script = script
    fake_client.result = _result('```json\n{"decision": "analyzed"}\n```')
    result = await _run(tmp_path, other_state, recorder)
    options = fake_client.options
    assert options.add_dirs == []
    assert "This run has no team rules." in options.system_prompt
    assert 'set="team"' not in options.system_prompt
    hunter = options.agents["duplicate_hunter"].prompt
    assert f"product `Core`: {OTHER['component']}" in hunter
    assert result.decision == "analyzed" and len(recorder.actions) == 1
    assert result.auto_apply is False


async def test_a_no_team_duplicate_of_a_meta_bug_fails_the_run(
    no_team, fake_client, tmp_path, other_state, monkeypatch, verdict_stores
):
    meta = {"id": DUPE_TARGET, "groups": [], "keywords": ["meta"], **OTHER}
    _fake_fetch(monkeypatch, {DUPE_TARGET: meta})
    recorder = ActionsRecorder()

    def script():
        _record_verdict(verdict_stores, "DUPLICATE", DUPE_TARGET)
        _duplicate_of_target(recorder)

    fake_client.script = script
    fake_client.result = _result('```json\n{"decision": "duplicate"}\n```')
    with pytest.raises(DupeTargetIsMeta, match=f"bug {DUPE_TARGET}"):
        await _run(tmp_path, other_state, recorder)


def test_a_team_run_is_not_bound_by_the_no_team_products(monkeypatch, other_bug):
    outside = Component(product="Thunderbird", component="General", trees=("comm/",))
    assert outside.product not in TERMS.no_team.products
    team = TEAM.model_copy(update={"components": (*TEAM.components, outside)})
    monkeypatch.setattr(preflight, "TEAM", team)
    bug = dict(other_bug, product="Thunderbird", component="General")
    assert skip_reason(TriageState(bug=bug, now=NOW)) is None


async def test_a_no_team_run_has_the_close_verifier(
    no_team, fake_client, tmp_path, other_state
):
    fake_client.result = _result('```json\n{"decision": "watching"}\n```')
    await _run(tmp_path, other_state)
    assert "close_verifier" in fake_client.options.agents
    flat = " ".join(full_prompt(other_state).split())
    assert "Confirm every close with `close_verifier` first." in flat
