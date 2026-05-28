from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from app import review_processor
from app.review_processor import ReviewSkipped, _score_and_gate
from app.reviewer_groups import Defaults, ReviewerGroup, ReviewerGroupsConfig

from bugbug.tools.code_review.scoring import Scores, ScoringResult
from bugbug.tools.code_review.test_coverage import ExistingCoverage

AUTHOR = "PHID-USER-x"


def _fake_patch(author_phid=AUTHOR):
    return SimpleNamespace(
        raw_diff="diff --git a/foo.js b/foo.js\n--- a/foo.js\n+++ b/foo.js\n@@ -1 +1 @@\n+x\n",
        patch_title="Tweak",
        summary=None,
        revision_id=123,
        author_phid=author_phid,
        has_bug=False,
        bug_id=0,
        reviewer_project_phids=["PHID-PROJ-x"],
    )


def _patch_common(monkeypatch, *, risk, complexity, group=None, members=frozenset()):
    if group is None:
        # Default: an enabled group with no author restriction, so the gating
        # tests exercise only the score threshold.
        group = ReviewerGroup(
            slug="ip-protection-reviewers",
            enabled=True,
            restrict_to_member_authors=False,
        )
    config = ReviewerGroupsConfig(
        defaults=Defaults(risk_threshold=3, complexity_threshold=3)
    )
    monkeypatch.setattr(review_processor, "get_reviewer_groups_config", lambda: config)
    monkeypatch.setattr(
        review_processor, "matching_groups", lambda patch: [group] if group else []
    )
    monkeypatch.setattr(
        review_processor, "resolve_project_phid", lambda slug: "PHID-PROJ-x"
    )
    monkeypatch.setattr(review_processor, "get_project_members", lambda phid: members)
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


# ---------------------------------------------------------------------------
# score threshold gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_skips_when_above_threshold(monkeypatch):
    _patch_common(monkeypatch, risk=5, complexity=1)
    review_request = SimpleNamespace(revision_id=123)

    with pytest.raises(ReviewSkipped) as exc:
        await _score_and_gate(_fake_patch(), review_request)

    assert exc.value.reason == "above_threshold"
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


@pytest.mark.asyncio
async def test_gate_uses_group_threshold_override(monkeypatch):
    group = ReviewerGroup(
        slug="ip-protection-reviewers",
        enabled=True,
        risk_threshold=6,
        restrict_to_member_authors=False,
    )
    _patch_common(monkeypatch, risk=5, complexity=1, group=group)
    review_request = SimpleNamespace(revision_id=123)

    _block, details = await _score_and_gate(_fake_patch(), review_request)
    assert details["thresholds"]["group"] == "ip-protection-reviewers"
    assert details["thresholds"]["risk"] == 6


# ---------------------------------------------------------------------------
# enablement + author gates (run before scoring)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_when_no_enabled_group(monkeypatch):
    disabled = ReviewerGroup(slug="ip-protection-reviewers", enabled=False)
    scorer = _patch_common(monkeypatch, risk=0, complexity=0, group=disabled)  # noqa: F841

    with pytest.raises(ReviewSkipped) as exc:
        await _score_and_gate(_fake_patch(), SimpleNamespace(revision_id=123))
    assert exc.value.reason == "not_enabled"


@pytest.mark.asyncio
async def test_skips_when_author_not_a_member(monkeypatch):
    group = ReviewerGroup(
        slug="ip-protection-reviewers", enabled=True, restrict_to_member_authors=True
    )
    _patch_common(
        monkeypatch,
        risk=0,
        complexity=0,
        group=group,
        members=frozenset({"PHID-USER-other"}),
    )

    with pytest.raises(ReviewSkipped) as exc:
        await _score_and_gate(_fake_patch(), SimpleNamespace(revision_id=123))
    assert exc.value.reason == "author_not_in_group"


@pytest.mark.asyncio
async def test_member_author_passes_membership_gate(monkeypatch):
    group = ReviewerGroup(
        slug="ip-protection-reviewers", enabled=True, restrict_to_member_authors=True
    )
    _patch_common(
        monkeypatch, risk=0, complexity=0, group=group, members=frozenset({AUTHOR})
    )

    block, _details = await _score_and_gate(
        _fake_patch(), SimpleNamespace(revision_id=123)
    )
    assert "<test_signals>" in block


@pytest.mark.asyncio
async def test_empty_membership_lookup_does_not_skip(monkeypatch):
    group = ReviewerGroup(
        slug="ip-protection-reviewers", enabled=True, restrict_to_member_authors=True
    )
    # Empty membership set (transient failure) — over-include rather than drop.
    _patch_common(monkeypatch, risk=0, complexity=0, group=group, members=frozenset())

    block, _details = await _score_and_gate(
        _fake_patch(), SimpleNamespace(revision_id=123)
    )
    assert "<test_signals>" in block


@pytest.mark.asyncio
async def test_opted_out_author_is_skipped(monkeypatch):
    group = ReviewerGroup(
        slug="ip-protection-reviewers",
        enabled=True,
        restrict_to_member_authors=False,
        opt_out=[AUTHOR],
    )
    _patch_common(monkeypatch, risk=0, complexity=0, group=group)

    with pytest.raises(ReviewSkipped) as exc:
        await _score_and_gate(_fake_patch(), SimpleNamespace(revision_id=123))
    assert exc.value.reason == "author_opted_out"
