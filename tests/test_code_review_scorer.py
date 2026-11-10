from types import SimpleNamespace

from bugbug.tools.code_review.scorer import (
    build_severity_breakdown,
    summarize_severity_breakdowns,
)
from bugbug.tools.core.data_types import CommentSeverity


def make_comment(severity):
    return SimpleNamespace(severity=severity)


def test_build_severity_breakdown_counts_matches_per_severity():
    critical = make_comment(CommentSeverity.CRITICAL)
    nit = make_comment(CommentSeverity.NIT)
    legacy = make_comment(None)

    breakdown = build_severity_breakdown(
        [critical, nit, legacy],
        matched_valid=[{"generated_comment": critical}],
        matched_invalid=[{"generated_comment": nit}],
    )

    assert breakdown["critical"] == {
        "generated": 1,
        "matched_valid": 1,
        "matched_invalid": 0,
    }, "The critical comment should be counted as generated and valid."
    assert breakdown["nit"] == {
        "generated": 1,
        "matched_valid": 0,
        "matched_invalid": 1,
    }, "The nit should be counted as generated and invalid."
    assert sum(counts["generated"] for counts in breakdown.values()) == 2, (
        "Comments without a severity should be left out."
    )


def test_summarize_severity_breakdowns_aggregates_rows():
    row = {
        "severity_breakdown": build_severity_breakdown(
            [make_comment(CommentSeverity.MAJOR), make_comment(CommentSeverity.MAJOR)],
            matched_valid=[{"generated_comment": make_comment(CommentSeverity.MAJOR)}],
            matched_invalid=[],
        )
    }

    summary = summarize_severity_breakdowns([row, row, {}])

    assert summary["major_generated"] == 4, (
        "Generated counts should be summed across rows."
    )
    assert summary["major_valid_match_rate"] == 0.5, (
        "The valid match rate should be valid matches over generated comments."
    )
    assert summary["critical_valid_match_rate"] == 0, (
        "Severities without comments should have a zero rate."
    )
