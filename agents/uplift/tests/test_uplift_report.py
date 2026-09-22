"""Tests for reading and publishing what the agent wrote.

`report.json` is model output, so what matters is that a plausible but wrong
value cannot get through, and that a bad report degrades to an unresolved run
rather than losing the whole one.
"""

from __future__ import annotations

import json

import pytest
from hackbot_agents.uplift_merge_conflict_resolver.agent import read_agent_report
from hackbot_agents.uplift_merge_conflict_resolver.models import Report

REPORT = {
    "resolved": True,
    "confidence": "high",
    "summary": "Rebased the import block.",
    "conflicts": [{"file": "a.cpp", "resolution": "kept both edits"}],
    "unresolved": [],
}


def write(scratch_out, report=None, summary=None):
    if report is not None:
        (scratch_out / "report.json").write_text(report)
    if summary is not None:
        (scratch_out / "summary.md").write_text(summary)


def test_the_agents_report_is_published_as_unverified(tmp_path, publisher):
    write(tmp_path, report=json.dumps(REPORT), summary="# resolved")

    result = read_agent_report(tmp_path, publisher)

    assert result == Report(**REPORT), (
        "The parsed report should match what was written."
    )
    assert publisher.calls == [
        ("summary.md", tmp_path / "summary.md", "text/markdown"),
        ("report.unverified.json", tmp_path / "report.json", "application/json"),
    ], (
        "The agent's own report is published under a name that says the checks "
        "have not been applied to it yet."
    )


@pytest.mark.parametrize(
    "body, reason",
    [
        ("{not json", "not JSON at all"),
        ("[1, 2, 3]", "a list rather than an object"),
        ('"a string"', "a bare string"),
        ("", "empty"),
        ('{"resolved": "false"}', "a stringified `false` `bool()` would read as true"),
        ('{"resolved": true, "confidence": "very high"}', "an invented confidence"),
    ],
)
def test_a_bad_report_is_never_read_as_resolved(tmp_path, body, reason):
    write(tmp_path, report=body)

    assert read_agent_report(tmp_path, None) == Report(), (
        f"A report that is {reason} should fall back to unresolved."
    )


def test_a_report_that_cannot_be_parsed_is_still_published(tmp_path, publisher):
    write(tmp_path, report="{broken")

    result = read_agent_report(tmp_path, publisher)

    assert result == Report(), "An unparseable report should fall back."
    assert publisher.bodies["report.unverified.json"] == "{broken", (
        "The raw report is still published, verbatim, so a human can inspect it."
    )


def test_a_missing_report_is_an_unresolved_run(tmp_path, publisher):
    result = read_agent_report(tmp_path, publisher)

    assert result == Report(), "A missing `report.json` should yield a bare report."
    assert publisher.calls == [], (
        "Nothing should be published when nothing was written."
    )


def test_reading_works_without_a_publisher(tmp_path):
    write(tmp_path, report='{"resolved": false}', summary="# nothing doing")

    assert read_agent_report(tmp_path, None) == Report(resolved=False), (
        "A standalone run has no uploader, and reading should not depend on one."
    )
