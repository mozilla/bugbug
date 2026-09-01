"""Tests for the structured plan the agent parses out of its own final message.

`may_apply_unattended` decides whether a run's recorded actions reach a real bug
with nobody in between, so it is covered as closely as the hooks are.
"""

import re
from pathlib import Path

from hackbot_agents.frontend_triage.agent import (
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
    TRIAGE_SCOPE,
    ScopedComponent,
    guidance_for,
    owners_for_path,
)


def _block(body: str) -> str:
    return f"Here is the plan.\n\n```json\n{body}\n```"


def test_the_system_prompt_renders():
    # system.md goes through str.format, so a literal brace in it must be doubled or
    # startup raises KeyError and the run never begins. The structured-output block is
    # where that happens.
    prompt = load_system_prompt(
        Path("rules"), "", guidance_for("Firefox", "New Tab Page"), ()
    )
    assert '"severity_assessment": {' in prompt
    assert "{rules_dir}" not in prompt
    assert "{triaged_components}" not in prompt
    # The component list reaches the prompt as full routing keys, since a bare component
    # name would not say which product it belongs to.
    assert "Firefox :: New Tab Page" in prompt
    assert "Toolkit :: Application Update" in prompt


def test_the_scope_lists_every_component_in_registry_order():
    # Asserted against a fixed registry rather than the real one, so this keeps testing
    # the rendering when TRIAGE_SCOPE changes.
    scope = (
        ScopedComponent("Firefox", "New Tab Page", "#one", trees=("browser/",)),
        ScopedComponent("Toolkit", "Application Update", "#two", trees=("toolkit/",)),
        ScopedComponent("Firefox", "Theme", "#one", trees=("browser/",)),
    )
    rendered = render_scope(scope)
    assert rendered.startswith(
        "- **Firefox :: New Tab Page**\n"
        "- **Toolkit :: Application Update**\n"
        "- **Firefox :: Theme**\n"
    )
    # Routing is notify.py's decision, and the agent has no tool to act on it.
    assert "#one" not in rendered


def test_the_scope_says_it_is_neither_a_limit_nor_a_vocabulary():
    # Two ways to misread a list of components in a system prompt, both expensive.
    # Reading it as exhaustive declares an in-scope bug out of scope, which is the
    # mistake ecea6ca6 was fixing. Reading it as a vocabulary gets a component adjusted
    # to match, and the component is the Slack routing key, so the notification then
    # goes nowhere without failing.
    rendered = render_scope()
    assert "not the limit" in rendered
    assert "verbatim" in rendered


def test_every_component_declares_a_tree():
    # `trees` is what `docs.docs_for` keys off and what the prompt's index renders, so a
    # component without one is routed but not triageable: the agent gets its name and no
    # idea where its code is, which is how a bug gets read as out of scope and skipped.
    for entry in TRIAGE_SCOPE:
        assert entry.trees, entry.key


def test_every_related_component_resolves():
    # `related` holds routing keys as strings, so a typo is only caught here.
    # `guidance_for` would raise KeyError mid-run, after the bug was already fetched.
    keys = {c.key for c in TRIAGE_SCOPE}
    for entry in TRIAGE_SCOPE:
        for related in entry.related:
            assert related in keys, f"{entry.key} -> {related}"


def test_an_unknown_component_gets_every_component():
    # `rules/scoping.md` puts an unlisted component in scope, so guessing one component
    # for it would leave the run with less than it has today. Failing open costs the
    # notes, which are a few kilobytes now, and nothing else.
    assert guidance_for("Firefox", "Graphics") == TRIAGE_SCOPE
    assert guidance_for(None, None) == TRIAGE_SCOPE


def test_only_the_matching_component_reaches_the_prompt():
    # The point of the split. Everything else stays reachable via the index and
    # `load_component_guidance`, but its notes are not paid for on every run.
    prompt = load_system_prompt(
        Path("rules"), "", guidance_for("Firefox", "New Tab Page"), ()
    )
    assert "stub and the full installer" not in prompt
    assert "State lives in the service" not in prompt
    # ...while the index still names every component, so a mislocalized bug is
    # recognisable as one.
    for entry in TRIAGE_SCOPE:
        assert f"**{entry.key}**" in prompt, entry.key


def test_a_nested_owner_wins_over_the_component_that_contains_it():
    # Ownership follows the most specific claim, or the hook never fires for the
    # components whose guidance matters most. The Fenix pair is the case this change
    # introduced: the homepage owns `…/fenix/home/`, and the toolbar owns
    # `…/fenix/home/toolbar/` inside it, because a bug in one really does localize into
    # the other.
    fenix = "mobile/android/fenix/app/src/main/java/org/mozilla/fenix"
    assert [o.component for o in owners_for_path(f"{fenix}/home/HomeFragment.kt")] == [
        "Homepage"
    ]
    assert [
        o.component
        for o in owners_for_path(f"{fenix}/home/toolbar/HomeToolbarComposable.kt")
    ] == ["Toolbar"]
    assert [
        o.component for o in owners_for_path("browser/installer/windows/nsis/stub.nsi")
    ] == ["Installer"]


def test_a_path_no_component_owns_belongs_to_no_component():
    # Load-bearing for `component_guidance_hook`: None means "no guidance exists", not
    # "guidance is missing", and must not be treated as something the agent can fetch.
    assert owners_for_path("gfx/thebes/gfxPlatform.cpp") == ()


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


# Paths written as `some/dir/File.ext` in a component's `notes` prose.
_GUIDANCE_PATH = re.compile(r"`([a-z][a-z0-9_./-]*/[A-Za-z0-9_./-]+)`")


def test_guidance_never_names_a_path_its_own_component_cannot_cite():
    # The invariant that keeps `component_guidance_hook` honest, and the one that caught
    # `browser/` being listed as owned: guidance told the agent where the prefs and
    # strings were, and citing them then had the comment refused. Every path a
    # component's own guidance names has to survive the hook for that component --
    # including across `related`, which is what makes Sharing's reference to
    # WebRTCParent legal.
    #
    # `trees` is checked alongside `notes` because the prompt now renders it, so it is
    # guidance the agent will act on just as much as the prose is.
    for entry in TRIAGE_SCOPE:
        loaded = {c.key for c in guidance_for(entry.product, entry.component)}
        for other in guidance_for(entry.product, entry.component):
            named = [m.group(1) for m in _GUIDANCE_PATH.finditer(other.notes)]
            named += list(other.trees)
            for path in named:
                # Mirrors `component_guidance_hook`, which passes when *any* owner is
                # loaded. Checking a single owner instead would fail on the trees the
                # three Android components share, for a citation the hook allows.
                owners = owners_for_path(path)
                assert not owners or any(o.key in loaded for o in owners), (
                    f"{entry.key}: guidance names {path}, "
                    f"owned by {' or '.join(o.key for o in owners)}"
                )


def test_the_bulk_of_a_split_tree_still_has_an_owner():
    # Regression guard for the areas-to-components change. One area used to own all of
    # `mobile/android/`, and replacing it with three components owning narrow packages
    # left most of Fenix unowned -- so `component_guidance_hook` stopped firing there and
    # a desktop bug could cite arbitrary Fenix code without loading Fenix guidance.
    fenix = "mobile/android/fenix/app/src/main/java/org/mozilla/fenix"
    assert owners_for_path(f"{fenix}/search/SearchFragment.kt")
    assert owners_for_path(
        "mobile/android/android-components/components/feature/addons/Addons.kt"
    )


def test_a_shared_tree_is_owned_by_every_component_that_claims_it():
    # Three components share `mobile/android/`, so ownership there is genuinely plural.
    # The hook passes when any owner is loaded; collapsing this to one would refuse a
    # Toolbar bug for citing a file its own team owns.
    owners = owners_for_path("mobile/android/fenix/app/src/main/AndroidManifest.xml")
    assert {o.component for o in owners} == {"History", "Toolbar", "Homepage"}


def test_a_narrow_owner_still_beats_the_tree_it_sits_in():
    # Plural ownership must not blunt the specific claim: `…/home/toolbar/` is the
    # toolbar's alone even though three components own `mobile/android/` above it.
    fenix = "mobile/android/fenix/app/src/main/java/org/mozilla/fenix"
    assert [o.component for o in owners_for_path(f"{fenix}/home/toolbar/X.kt")] == [
        "Toolbar"
    ]
    assert [o.component for o in owners_for_path(f"{fenix}/home/HomeFragment.kt")] == [
        "Homepage"
    ]


def test_a_file_owner_does_not_own_paths_that_merely_start_with_it():
    # `owns` entries that name a file are matched by prefix, so without a boundary check
    # `SitePermissions.sys.mjs` also claimed `SitePermissions.sys.mjs.bak`. No such path
    # is in the tree today; this keeps it that way.
    assert owners_for_path("browser/modules/SitePermissions.sys.mjs")
    assert owners_for_path("browser/modules/SitePermissions.sys.mjs.bak") == ()


def test_a_directory_is_spelled_with_a_trailing_slash_and_a_file_without():
    # `docs.docs_for` and `owners_for_path` both decide file-versus-directory from the
    # trailing slash. That is only safe if the convention actually holds, and sniffing for
    # a dot instead is what let `widget/foo.bar/` inherit a sibling's documentation.
    #
    # Both directions matter, and the second is the dangerous one: a file written *with*
    # a trailing slash turns an exact-match claim into a prefix claim that matches
    # nothing, so the component silently stops owning the file it named.
    for entry in TRIAGE_SCOPE:
        for value in (*entry.trees, *entry.doc_trees, *entry.owns):
            basename = value.rstrip("/").rsplit("/", 1)[-1]
            assert basename, f"{entry.key}: {value!r} has no final segment"
            if "." in basename:
                assert not value.endswith("/"), (
                    f"{entry.key}: {value} names a file but ends in a slash"
                )
            else:
                assert value.endswith("/"), (
                    f"{entry.key}: {value} names a directory but has no trailing slash"
                )


def test_a_tie_can_only_mean_two_components_claimed_the_same_path():
    # What makes a plural `owners_for_path` result co-ownership rather than ambiguity:
    # every component in it declared the same entry, so either team's guidance covers the
    # file. Probing each declared claim as a path exercises `_owns` rather than asserting
    # string arithmetic, so this fails if `_owns` ever grows a matching mode -- globbing,
    # case folding -- under which unrelated claims can tie on length.
    for entry in TRIAGE_SCOPE:
        for claim in entry.owns:
            owners = owners_for_path(claim)
            assert entry in owners, f"{entry.key} does not own its own claim {claim}"
            if len(owners) > 1:
                for other in owners:
                    assert claim in other.owns, (
                        f"{other.key} ties on {claim} without claiming it"
                    )


def test_ordinary_desktop_chrome_is_owned_by_nobody():
    # `browser/` and `toolkit/` contain almost every triaged component, so treating
    # either as owned would refuse comments for the ordinary reason that a Firefox bug
    # touches a Firefox file.
    for path in (
        "browser/base/content/browser.js",
        "browser/app/profile/firefox.js",
        "toolkit/content/widgets/panel-list.js",
        "widget/cocoa/nsCocoaWindow.mm",
    ):
        assert owners_for_path(path) == (), path
