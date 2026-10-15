from types import SimpleNamespace

import pytest
from hackbot_agents.test_repair import __main__ as entry
from hackbot_agents.test_repair.__main__ import _checkout_pin, _record_backout
from hackbot_agents.test_repair.agent import TestRepairResult
from hackbot_agents.test_repair.resolve import CommitRange, Investigation


def _investigation(**kwargs):
    defaults = dict(
        project="autoland",
        hg_revision="hgrev",
        harness="mochitest",
        platform="linux1804-64-qr/opt",
        failing_groups=[],
        commit_range=CommitRange(head="headsha", span=3),
    )
    return Investigation(**{**defaults, **kwargs})


def test_checkout_pin_returns_ref_and_depth():
    ref, depth = _checkout_pin(_investigation())
    assert ref == "headsha"
    # Depth spans the 3 commits in the range plus the base's parent.
    assert depth == 4


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


@pytest.fixture
def backouts(monkeypatch):
    calls = []
    monkeypatch.setattr(
        entry, "record_backout", lambda *args, **kwargs: calls.append(kwargs)
    )
    return calls


def _verdict(**kwargs):
    defaults = dict(
        num_turns=1,
        classification="regression",
        recommendation="backout",
        culprit_commit="c" * 40,
        summary="broke test",
    )
    return TestRepairResult(**{**defaults, **kwargs})


def test_a_backout_verdict_proposes_a_backout_on_autoland(backouts, tmp_path):
    ctx = SimpleNamespace(actions=object())
    _record_backout(ctx, _verdict(), _investigation(), tmp_path)

    assert backouts == [
        {
            "lando_repo": "firefox-autoland",
            "commit": "c" * 40,
            "reason": "mochitest failures",
            "reasoning": "broke test",
        }
    ]


@pytest.mark.parametrize(
    ("result", "project"),
    [
        (_verdict(recommendation="do_not_backout"), "autoland"),
        (_verdict(recommendation="rerun"), "autoland"),
        (_verdict(), "mozilla-central"),
    ],
)
def test_no_backout_is_proposed_otherwise(backouts, tmp_path, result, project):
    ctx = SimpleNamespace(actions=object())
    _record_backout(ctx, result, _investigation(project=project), tmp_path)

    assert backouts == []
