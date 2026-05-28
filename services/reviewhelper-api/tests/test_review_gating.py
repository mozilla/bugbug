from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app import review_processor
from app.review_processor import ReviewSkipped, _score_and_gate
from app.reviewer_groups import Defaults, ReviewerGroup, ReviewerGroupsConfig

from bugbug.tools.code_review.scoring import Scores, ScoringResult
from bugbug.tools.code_review.test_coverage import ExistingCoverage


def _fake_patch():
    return SimpleNamespace(
        raw_diff="diff --git a/foo.js b/foo.js\n--- a/foo.js\n+++ b/foo.js\n@@ -1 +1 @@\n+x\n",
        patch_title="Tweak",
        summary=None,
        revision_id=123,
        author_phid="PHID-USER-x",
        has_bug=False,
        bug_id=0,
        reviewer_project_phids=[],
    )


def _patch_common(monkeypatch, *, risk, complexity, group=None):
    config = ReviewerGroupsConfig(
        defaults=Defaults(risk_threshold=3, complexity_threshold=3)
    )
    monkeypatch.setattr(review_processor, "get_reviewer_groups_config", lambda: config)
    monkeypatch.setattr(
        review_processor, "matching_groups", lambda patch: [group] if group else []
    )
    monkeypatch.setattr(
        review_processor,
        "lookup_existing_coverage",
        AsyncMock(return_value=ExistingCoverage(coverage_signal="uncovered")),
    )
    scorer = SimpleNamespace(
        run=AsyncMock(
            return_value=ScoringResult(
                scores=Scores(risk=risk, complexity=complexity),
                model="claude-haiku-4-5",
                usage={"input_tokens": 10},
            )
        )
    )
    monkeypatch.setattr(review_processor, "get_risk_scorer", lambda: scorer)
    return config


@pytest.mark.asyncio
async def test_gate_skips_when_above_threshold(monkeypatch):
    _patch_common(monkeypatch, risk=5, complexity=1)
    review_request = SimpleNamespace(revision_id=123)

    with pytest.raises(ReviewSkipped) as exc:
        await _score_and_gate(_fake_patch(), review_request)

    assert exc.value.reason == "above_threshold"
    # Scores are recorded on the request even though we skipped.
    assert review_request.risk == 5
    assert review_request.complexity == 1
    assert review_request.coverage_signal == "uncovered"


@pytest.mark.asyncio
async def test_gate_passes_when_below_threshold(monkeypatch):
    _patch_common(monkeypatch, risk=1, complexity=2)
    review_request = SimpleNamespace(revision_id=123)

    block, details = await _score_and_gate(_fake_patch(), review_request)

    assert "<test_signals>" in block
    assert details["scoring_model"] == "claude-haiku-4-5"
    assert review_request.risk == 1
    assert review_request.complexity == 2


@pytest.mark.asyncio
async def test_gate_uses_group_threshold_override(monkeypatch):
    group = ReviewerGroup(slug="ip-protection-reviewers", risk_threshold=6)
    _patch_common(monkeypatch, risk=5, complexity=1, group=group)
    review_request = SimpleNamespace(revision_id=123)

    # risk=5 would skip under the default threshold of 3, but the group raised
    # it to 6, so the review proceeds.
    block, details = await _score_and_gate(_fake_patch(), review_request)
    assert details["thresholds"]["group"] == "ip-protection-reviewers"
    assert details["thresholds"]["risk"] == 6
