# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bugbug.tools.code_review.diff_analysis import (
    analyze_diff,
    format_test_signal_block,
)
from bugbug.tools.code_review.scoring import RiskComplexityScorer, Scores
from bugbug.tools.code_review.test_coverage import (
    ExistingCoverage,
    format_coverage_block,
    lookup_existing_coverage,
)


def _diff(*files: str) -> str:
    return "\n".join(files)


def _git_file(path: str, added: int = 1, removed: int = 0) -> str:
    body = "\n".join(["+new line"] * added + ["-old line"] * removed)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,{removed or 1} +1,{added or 1} @@\n"
        f"{body}"
    )


# ---------------------------------------------------------------------------
# diff_analysis
# ---------------------------------------------------------------------------


def test_no_code_change():
    assert analyze_diff("").in_diff_test_signal == "no_code_change"


def test_tests_only():
    diff = _git_file("browser/components/foo/test/browser_foo.js", added=5)
    stats = analyze_diff(diff)
    assert stats.in_diff_test_signal == "tests_only"
    assert stats.non_test_files_changed == 0
    assert stats.test_files_changed == 1


def test_absent_when_only_non_test_code():
    diff = _git_file("browser/components/foo/Foo.sys.mjs", added=20)
    stats = analyze_diff(diff)
    assert stats.in_diff_test_signal == "absent"
    assert stats.non_test_paths == ["browser/components/foo/Foo.sys.mjs"]


def test_sparse_low_test_ratio():
    diff = _diff(
        _git_file("browser/components/foo/Foo.sys.mjs", added=100),
        _git_file("browser/components/foo/test/test_foo.js", added=5),
    )
    assert analyze_diff(diff).in_diff_test_signal == "sparse"


def test_adequate_high_test_ratio():
    diff = _diff(
        _git_file("browser/components/foo/Foo.sys.mjs", added=10),
        _git_file("browser/components/foo/test/test_foo.js", added=10),
    )
    assert analyze_diff(diff).in_diff_test_signal == "adequate"


def test_format_test_signal_block_includes_coverage():
    stats = analyze_diff(_git_file("a/b/C.cpp", added=3))
    block = format_test_signal_block(stats, coverage_block="coverage_signal=uncovered")
    assert "<test_signals>" in block
    assert "in_diff_test_signal=absent" in block
    assert "coverage_signal=uncovered" in block
    assert block.rstrip().endswith("</test_signals>")


# ---------------------------------------------------------------------------
# test_coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_no_non_test_files():
    result = await lookup_existing_coverage([])
    assert result.coverage_signal == "no_non_test_files"


@pytest.mark.asyncio
async def test_lookup_partial_coverage():
    async def fake_search(query, tests, limit):
        if query == "Covered":
            return [("dom/tests/test_covered.js", 10, "...")]
        return []

    client = AsyncMock()
    client.search.side_effect = fake_search
    with patch(
        "bugbug.tools.code_review.test_coverage._get_client", return_value=client
    ):
        result = await lookup_existing_coverage(
            ["dom/base/Covered.cpp", "dom/base/Uncovered.cpp"]
        )
    assert result.coverage_signal == "partial"
    assert "dom/base/Covered.cpp" in result.covered_paths
    assert "dom/base/Uncovered.cpp" in result.uncovered_paths


@pytest.mark.asyncio
async def test_lookup_searchfox_error_degrades():
    client = AsyncMock()
    client.search.side_effect = Exception("searchfox down")
    with patch(
        "bugbug.tools.code_review.test_coverage._get_client", return_value=client
    ):
        result = await lookup_existing_coverage(["dom/base/Element.cpp"])
    assert result.coverage_signal == "skipped_searchfox_error"


@pytest.mark.asyncio
async def test_lookup_large_diff_skipped():
    client = AsyncMock()
    client.search.return_value = []
    paths = [f"dom/base/File{i}.cpp" for i in range(15)]
    with patch(
        "bugbug.tools.code_review.test_coverage._get_client", return_value=client
    ):
        result = await lookup_existing_coverage(paths, file_cap=10)
    assert result.coverage_signal == "skipped_large_diff"
    assert result.candidate_count == 15


def test_format_coverage_block_none_when_no_files():
    assert (
        format_coverage_block(ExistingCoverage(coverage_signal="no_non_test_files"))
        is None
    )


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_risk_scorer_parses_and_captures_usage():
    fake_llm = MagicMock()
    structured = AsyncMock()
    fake_llm.with_structured_output.return_value = structured
    scorer = RiskComplexityScorer(fake_llm, "claude-haiku-4-5")

    raw = SimpleNamespace(
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "input_token_details": {"cache_read": 10, "cache_creation": 5},
        }
    )
    structured.ainvoke.return_value = {
        "parsed": Scores(risk=2, complexity=1, risk_factors=["leaf UI"]),
        "raw": raw,
    }

    result = await scorer.run(
        title="Tweak button",
        summary=None,
        revision_id=42,
        author="PHID-USER-x",
        bug_id=None,
        raw_diff="diff --git a/a b/a",
        test_signals_block="<test_signals>...</test_signals>",
    )

    assert result.scores.risk == 2
    assert result.scores.complexity == 1
    assert result.model == "claude-haiku-4-5"
    assert result.usage["input_tokens"] == 100
    assert result.usage["cache_read_input_tokens"] == 10
    assert result.usage["cache_creation_input_tokens"] == 5


@pytest.mark.asyncio
async def test_risk_scorer_raises_on_parse_failure():
    from bugbug.tools.core.exceptions import ModelResultError

    fake_llm = MagicMock()
    structured = AsyncMock()
    fake_llm.with_structured_output.return_value = structured
    scorer = RiskComplexityScorer(fake_llm, "claude-haiku-4-5")
    structured.ainvoke.return_value = {
        "parsed": None,
        "raw": SimpleNamespace(),
        "parsing_error": "bad",
    }

    with pytest.raises(ModelResultError):
        await scorer.run(
            title="t",
            summary=None,
            revision_id=1,
            author="a",
            bug_id=None,
            raw_diff="d",
        )
