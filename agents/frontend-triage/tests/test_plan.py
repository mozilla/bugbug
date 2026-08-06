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
)


def _block(body: str) -> str:
    return f"Here is the plan.\n\n```json\n{body}\n```"


def test_the_system_prompt_renders():
    # system.md goes through str.format, so a literal brace in it must be doubled or
    # startup raises KeyError and the run never begins. The prompt now has to show
    # JSON payloads like {"add": [...]}, which is where that would happen.
    prompt = load_system_prompt(Path("rules"), "")
    assert '{"add": ["…"]}' in prompt
    assert "{rules_dir}" not in prompt


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
