"""Tests for the structured plan the agent parses out of its own final message.

`may_apply_unattended` decides whether a run's recorded actions reach a real bug
with nobody in between, so it is covered as closely as the hooks are.
"""

from pathlib import Path

from hackbot_agents.frontend_triage.agent import (
    load_system_prompt,
    may_apply_unattended,
    parse_confidence,
    parse_plan,
    render_scope,
)
from hackbot_agents.frontend_triage.config import TRIAGE_SCOPE, ScopedComponent


def _block(body: str) -> str:
    return f"Here is the plan.\n\n```json\n{body}\n```"


def test_the_system_prompt_renders():
    # system.md goes through str.format, so a literal brace in it must be doubled or
    # startup raises KeyError and the run never begins. The prompt now has to show
    # JSON payloads like {"add": [...]}, which is where that would happen.
    prompt = load_system_prompt(Path("rules"), "")
    assert '{"add": ["…"]}' in prompt
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


def test_every_area_has_prompt_guidance():
    # The registry is what makes a component triaged; this is what makes it triageable.
    # `Source repository` carries the per-area code layout, and an area with no bullet
    # there means the agent is pointed at a component with no idea where its code lives
    # -- which is how a bug gets read as out of scope and skipped. So a new area costs
    # two files, visibly, rather than one file plus a prompt nobody remembered.
    prompt = load_system_prompt(Path("rules"), "")
    source_section = prompt.split("# Source repository", 1)[1]
    for area in {entry.area for entry in TRIAGE_SCOPE}:
        assert f"**{area}**" in source_section, area


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
