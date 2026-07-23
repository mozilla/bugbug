import os

from hackbot_agents.test_repair import logs
from hackbot_agents.test_repair.__main__ import _pin_checkout
from hackbot_agents.test_repair.agent import TestRepairResult


def test_sanitize_log_keeps_failure_and_error_lines():
    raw = "\n".join(
        [
            "INFO - starting test",
            "TEST-UNEXPECTED-FAIL | dom/test_a.js | assertion failed",
            "some noise",
            "12:00 ERROR - linker error",
            "TEST-PASS | dom/test_b.js",
            "FATAL - crash",
        ]
    )
    out = logs.sanitize_log(raw).splitlines()
    assert any("TEST-UNEXPECTED-FAIL" in line for line in out)
    assert any("ERROR - linker error" in line for line in out)
    assert any("FATAL - crash" in line for line in out)
    # Passing/info lines are dropped.
    assert all("TEST-PASS" not in line for line in out)
    assert all("starting test" not in line for line in out)


def test_sanitize_log_dedupes_consecutive_repeats():
    raw = "\n".join(["TEST-UNEXPECTED-FAIL | x | boom"] * 3)
    assert len(logs.sanitize_log(raw).splitlines()) == 1


def test_pin_checkout_sets_ref_and_depth(monkeypatch):
    monkeypatch.delenv("SOURCE_REF", raising=False)
    monkeypatch.delenv("SOURCE_DEPTH", raising=False)
    _pin_checkout(["headsha", "midsha", "oldsha"])
    assert os.environ["SOURCE_REF"] == "headsha"
    # Depth spans the 3 candidates plus one so the last-green parent is reachable.
    assert os.environ["SOURCE_DEPTH"] == "4"


def test_result_model_serializes_findings():
    result = TestRepairResult(
        num_turns=5,
        total_cost_usd=0.42,
        classification="regression",
        recommendation="backout",
        culprit_commit="deadbeef",
        confidence=0.8,
        summary="broke test",
        analysis="the diff removed a null check",
    )
    findings = result.model_dump()
    assert findings["classification"] == "regression"
    assert findings["recommendation"] == "backout"
    assert findings["culprit_commit"] == "deadbeef"
    assert findings["proposed_patch"] is False


def test_intermittent_result_defaults():
    result = TestRepairResult(
        num_turns=2,
        classification="intermittent",
        recommendation="do_not_backout",
    )
    assert result.culprit_commit is None
    assert result.proposed_patch is False
