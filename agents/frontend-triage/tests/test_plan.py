"""Tests for the structured plan the agent parses out of its own final message.

`may_apply_unattended` decides whether a run's recorded actions reach a real bug
with nobody in between, so it is covered as closely as the hooks are.
"""

import re
from pathlib import Path

from hackbot_agents.frontend_triage.agent import (
    AREAS_DIR,
    load_system_prompt,
    may_apply_unattended,
    parse_bug_id,
    parse_confidence,
    parse_duplicate_assessment,
    parse_plan,
    parse_severity,
    parse_severity_assessment,
    render_scope,
)
from hackbot_agents.frontend_triage.config import (
    AREAS,
    TRIAGE_SCOPE,
    ScopedComponent,
    area_for_path,
    areas_for,
)


def _block(body: str) -> str:
    return f"Here is the plan.\n\n```json\n{body}\n```"


def test_the_system_prompt_renders():
    # system.md goes through str.format, so a literal brace in it must be doubled or
    # startup raises KeyError and the run never begins. The structured-output block is
    # where that happens.
    prompt = load_system_prompt(Path("rules"), "", areas_for("Firefox", "New Tab Page"))
    assert '"severity_assessment": {' in prompt
    assert "{rules_dir}" not in prompt
    assert "{triaged_components}" not in prompt
    # The component list reaches the prompt as full routing keys, since a bare component
    # name would not say which product it belongs to.
    assert "Firefox :: New Tab Page" in prompt
    assert "Toolkit :: Application Update" in prompt


def test_the_scope_is_grouped_by_area_in_registry_order():
    # Asserted against a fixed registry rather than the real one, so this keeps testing
    # the grouping when TRIAGE_SCOPE changes.
    scope = (
        ScopedComponent("Firefox", "New Tab Page", "Desktop", "#one"),
        ScopedComponent("Toolkit", "Application Update", "Updater", "#two"),
        ScopedComponent("Firefox", "Theme", "Desktop", "#one"),
    )
    rendered = render_scope(scope)
    assert rendered.startswith(
        "- **Desktop** — Firefox :: New Tab Page, Firefox :: Theme.\n"
        "- **Updater** — Toolkit :: Application Update.\n"
    )


def test_the_scope_says_it_is_neither_a_limit_nor_a_vocabulary():
    # Two ways to misread a list of components in a system prompt, both expensive.
    # Reading it as exhaustive declares an in-scope bug out of scope, which is the
    # mistake ecea6ca6 was fixing. Reading it as a vocabulary gets a component adjusted
    # to match, and the component is the Slack routing key, so the notification then
    # goes nowhere without failing.
    rendered = render_scope()
    assert "not the limit" in rendered
    assert "verbatim" in rendered


def test_every_area_has_a_guidance_file():
    # The registry is what makes a component triaged; this is what makes it triageable.
    # An area whose file is missing points the agent at a component with no idea where
    # its code lives -- which is how a bug gets read as out of scope and skipped. So a
    # new area costs two files, visibly, rather than one file plus a prompt nobody
    # remembered.
    for area in AREAS:
        assert (AREAS_DIR / f"{area.slug}.md").is_file(), area.name


def test_every_registry_area_resolves():
    # `area` and `related_areas` are strings, so a typo in either is only caught here.
    # `areas_for` would raise KeyError mid-run, after the bug was already fetched.
    names = {a.name for a in AREAS}
    for entry in TRIAGE_SCOPE:
        assert entry.area in names, entry.key
        for related in entry.related_areas:
            assert related in names, f"{entry.key} -> {related}"


def test_an_unknown_component_gets_every_area():
    # `rules/scoping.md` puts an unlisted component in scope, so guessing one area for
    # it would leave the run with less than it has today. Failing open costs the old
    # prompt size and nothing else.
    assert areas_for("Firefox", "Graphics") == AREAS
    assert areas_for(None, None) == AREAS


def test_only_the_matching_area_reaches_the_prompt():
    # The point of the split. Everything else stays reachable via the index and
    # `load_area_guidance`, but its text is not paid for on every run.
    prompt = load_system_prompt(Path("rules"), "", areas_for("Firefox", "New Tab Page"))
    assert "NSIS" not in prompt
    assert "IPProtectionPanel.sys.mjs" not in prompt
    # ...while the index still names every area, so a mislocalized bug is recognisable.
    for area in AREAS:
        assert f"**{area.name}**" in prompt, area.name


def test_an_owned_subtree_resolves_even_though_a_broader_area_describes_it():
    # The desktop frontend's index entry covers `browser/`, but the installer and IP
    # Protection sit inside it and own their own subtrees. Ownership has to follow the
    # specific claim, or the hook never fires for the areas whose guidance matters most.
    assert area_for_path("browser/installer/windows/nsis/stub.nsi").name == (
        "Windows installer"
    )
    assert area_for_path(
        "browser/components/ipprotection/IPProtection.sys.mjs"
    ).name == ("IP Protection")


def test_a_path_in_no_area_belongs_to_no_area():
    # Load-bearing for `area_guidance_hook`: None means "no guidance exists", not
    # "guidance is missing", and must not be treated as something the agent can fetch.
    assert area_for_path("gfx/thebes/gfxPlatform.cpp") is None


def test_confidence_is_normalized():
    # The value comes out of the agent's free-form JSON block and decides whether the
    # actions are posted, so "High" must not read as "not high".
    for raw in ("high", "High", "HIGH", "  high ", "\nHigh\t"):
        assert parse_confidence(raw) == "high"
    assert parse_confidence("Medium") == "medium"
    assert parse_confidence("LOW") == "low"


def test_an_unusable_confidence_is_none():
    for raw in ("very-high", "", "  ", "h", None, 42, True, ["high"], {"high": 1}):
        assert parse_confidence(raw) is None


def test_parse_plan_carries_the_product_and_component():
    # They route the Slack notification (see notify.py) and come from nowhere else --
    # the run's inputs are just a bug id.
    plan = parse_plan(_block('{"product": " Firefox ", "component": "New Tab Page"}'))
    assert plan["product"] == "Firefox"
    assert plan["component"] == "New Tab Page"


def test_parse_plan_drops_an_unusable_product_or_component():
    # A non-string would fail FrontendTriageResult's validation and lose the whole
    # run's result over a field that only picks a channel.
    plan = parse_plan(_block('{"product": 42, "component": ["New Tab Page"]}'))
    assert plan["product"] is None
    assert plan["component"] is None
    plan = parse_plan(_block('{"summary": "s"}'))
    assert plan["product"] is None
    assert plan["component"] is None


def test_parse_plan_normalizes_confidence():
    plan = parse_plan(_block('{"summary": "s", "confidence": "High"}'))
    assert plan["confidence"] == "high"


def test_parse_plan_drops_an_unusable_confidence():
    plan = parse_plan(_block('{"summary": "s", "confidence": "quite sure"}'))
    assert plan["confidence"] is None


def test_only_a_high_confidence_plan_applies_unattended():
    assert may_apply_unattended({"confidence": "high"})
    assert not may_apply_unattended({"confidence": "medium"})
    assert not may_apply_unattended({"confidence": "low"})


def test_an_out_of_scope_run_does_not_apply_unattended():
    # `rules/scoping.md` pairs out-of-scope with `actionable: false` *and*
    # `confidence: low`, but nothing makes the agent pair them, so a high +
    # actionable:false run would post an out-of-scope note on the strength of the
    # confidence alone.
    assert not may_apply_unattended({"confidence": "high", "actionable": False})
    # A run that doesn't report `actionable` at all is not held back by it.
    assert may_apply_unattended({"confidence": "high", "actionable": True})
    assert may_apply_unattended({"confidence": "high", "actionable": None})


def test_a_plan_that_did_not_parse_does_not_apply_unattended():
    # Fails closed: a plan with no `confidence` is not a `high`.
    for text in (
        None,
        "",
        "no json here",
        "```json\nnot json\n```",
        "```json\n[]\n```",
    ):
        assert not may_apply_unattended(parse_plan(text)), text
    assert not may_apply_unattended({})


def test_an_unusable_confidence_does_not_apply_unattended():
    # Normalization in `parse_plan` is what makes the `==` comparison safe. Without
    # it "High" falls through to False and a well-formed high-confidence run is held.
    plan = parse_plan(_block('{"confidence": "High", "actionable": true}'))
    assert may_apply_unattended(plan)
    plan = parse_plan(_block('{"confidence": "highish", "actionable": true}'))
    assert not may_apply_unattended(plan)


def test_severity_is_normalized():
    for value in ("S4", "s4", " S4 ", "s4\n"):
        assert parse_severity(value) == "S4", value


def test_an_unusable_severity_is_none():
    # Bugzilla's legacy word forms and the unset markers are not levels this agent
    # deals in, so they fail closed rather than reaching a bug as a suggestion.
    for value in ("critical", "normal", "S5", "--", "N/A", "", "   ", None, 4, ["S4"]):
        assert parse_severity(value) is None, value


def test_the_severity_assessment_is_normalized():
    assessment = parse_severity_assessment(
        {"suggested": " s2 ", "confidence": "High", "rationale": "no workaround"}
    )
    assert assessment.suggested == "S2"
    # "High" would otherwise read as "not high" and silently drop both the comment
    # block and the Slack marker.
    assert assessment.confidence == "high"
    assert assessment.rationale == "no workaround"


def test_each_field_degrades_on_its_own():
    # Discarding the rationale along with an unusable level would throw away the half a
    # human reads, and a non-string rationale would otherwise raise after the whole
    # expensive run and lose the entire plan.
    unusable_level = parse_severity_assessment(
        {"suggested": "critical", "confidence": "nope", "rationale": "cosmetic only"}
    )
    assert (unusable_level.suggested, unusable_level.confidence) == (None, None)
    assert unusable_level.rationale == "cosmetic only"

    unusable_rationale = parse_severity_assessment(
        {"suggested": "S3", "rationale": {"a": 1}}
    )
    assert unusable_rationale.suggested == "S3"
    assert unusable_rationale.rationale is None


def test_an_unusable_severity_assessment_is_none():
    for value in (None, [], "S3", 3):
        assert parse_severity_assessment(value) is None, value


def test_parse_plan_normalizes_the_severity_assessment():
    plan = parse_plan(
        _block(
            '{"confidence": "high", "severity_assessment": '
            '{"suggested": "s1", "confidence": "MEDIUM", "rationale": "data loss"}}'
        )
    )
    assessment = plan["severity_assessment"]
    assert assessment.suggested == "S1"
    assert assessment.confidence == "medium"
    assert assessment.rationale == "data loss"


def test_a_bug_id_is_normalized():
    # It comes out of free-form JSON, so the string form means the same thing.
    for value in (1998432, "1998432", " 1998432 "):
        assert parse_bug_id(value) == 1998432


def test_an_unusable_bug_id_is_none():
    # `True` is an int subclass in Python and would otherwise coerce to bug 1.
    for value in (0, -5, True, False, None, "bug 1998432", "", "1.5", [1998432]):
        assert parse_bug_id(value) is None, value


def test_the_duplicate_assessment_is_normalized():
    assessment = parse_duplicate_assessment(
        {"duplicate_of": "1998432", "confidence": "High", "rationale": "same selector"}
    )
    assert assessment.duplicate_of == 1998432
    assert assessment.confidence == "high"
    assert assessment.rationale == "same selector"


def test_a_found_nothing_verdict_is_kept_rather_than_dropped():
    # `duplicate_of: null` is the answer we are measuring: it says the hunt ran and
    # found nothing, which has to stay distinguishable from never having run.
    assessment = parse_duplicate_assessment(
        {"duplicate_of": None, "confidence": "high", "rationale": "nothing similar"}
    )
    assert assessment is not None
    assert assessment.duplicate_of is None
    assert assessment.rationale == "nothing similar"


def test_an_unusable_duplicate_assessment_is_none():
    for value in (None, [], "1998432", 3):
        assert parse_duplicate_assessment(value) is None, value


def test_parse_plan_normalizes_the_duplicate_assessment():
    plan = parse_plan(
        _block(
            '{"confidence": "high", "duplicate_assessment": '
            '{"duplicate_of": "1998432", "confidence": "MEDIUM", "rationale": "same"}}'
        )
    )
    assessment = plan["duplicate_assessment"]
    assert assessment.duplicate_of == 1998432
    assert assessment.confidence == "medium"


def test_a_duplicate_verdict_does_not_change_what_reaches_a_bug():
    # The load-bearing never-gate assertion. Finding a duplicate must not suppress the
    # triage, so it must not move `may_apply_unattended` in either direction.
    base = {"confidence": "high", "actionable": True}
    found = {**base, "duplicate_assessment": {"duplicate_of": 1998432}}
    none_found = {**base, "duplicate_assessment": {"duplicate_of": None}}
    assert may_apply_unattended(base)
    assert may_apply_unattended(found)
    assert may_apply_unattended(none_found)


# Paths written as `some/dir/File.ext` in an area's guidance prose.
_GUIDANCE_PATH = re.compile(r"`([a-z][a-z0-9_./-]*/[A-Za-z0-9_./-]+)`")


def test_guidance_never_names_a_path_its_own_component_cannot_cite():
    # The invariant that keeps `area_guidance_hook` honest, and the one that caught
    # `browser/` being listed as owned: an area told the agent where the prefs and
    # strings were, and citing them then had the comment refused. Every path a
    # component's own guidance names has to survive the hook for that component --
    # including across `related_areas`, which is what makes Sharing's reference to
    # WebRTCParent legal.
    for entry in TRIAGE_SCOPE:
        areas = areas_for(entry.product, entry.component)
        loaded = {a.name for a in areas}
        for area in areas:
            text = (AREAS_DIR / f"{area.slug}.md").read_text()
            for match in _GUIDANCE_PATH.finditer(text):
                owner = area_for_path(match.group(1))
                assert owner is None or owner.name in loaded, (
                    f"{entry.key}: guidance names {match.group(1)}, "
                    f"owned by {owner.name if owner else None}"
                )


def test_ordinary_desktop_chrome_is_owned_by_nobody():
    # `browser/` and `toolkit/` describe the desktop frontend usefully in the index but
    # contain almost every other area, so treating them as owned refuses comments for
    # the ordinary reason that a Firefox bug touches a Firefox file.
    for path in (
        "browser/base/content/browser.js",
        "browser/app/profile/firefox.js",
        "toolkit/content/widgets/panel-list.js",
        "widget/cocoa/nsCocoaWindow.mm",
    ):
        assert area_for_path(path) is None, path
