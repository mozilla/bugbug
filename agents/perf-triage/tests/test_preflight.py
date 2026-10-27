from __future__ import annotations

import pytest
from hackbot_agents.perf_triage.preflight import profile_urls, write_mode


@pytest.mark.parametrize(
    "value,mode",
    [
        ("?", "full"),
        ("---", "full"),
        ("", "full"),
        (None, "full"),
        ("none", "comment_only"),
        ("low", "comment_only"),
        ("high", "comment_only"),
    ],
)
def test_write_mode(value, mode):
    assert write_mode({"cf_performance_impact": value}) == mode


def test_write_mode_missing_field_is_full():
    assert write_mode({}) == "full"


def test_profile_urls_found_in_url_field_then_comments():
    bug = {"url": "https://profiler.firefox.com/public/abc123/calltree/?v=10"}
    comments = ["see https://share.firefox.dev/44agmdV."]
    assert profile_urls(bug, comments) == [
        "https://profiler.firefox.com/public/abc123/calltree/?v=10",
        "https://share.firefox.dev/44agmdV",
    ]


def test_profile_urls_deduplicated():
    comments = ["https://share.firefox.dev/x1 and again https://share.firefox.dev/x1"]
    assert profile_urls({}, comments) == ["https://share.firefox.dev/x1"]


def test_from_url_profiles_count():
    comments = ["(https://profiler.firefox.com/from-url/https%3A%2F%2Fx/calltree)"]
    assert profile_urls({}, comments) == [
        "https://profiler.firefox.com/from-url/https%3A%2F%2Fx/calltree"
    ]


def test_profiler_home_page_is_not_a_profile():
    assert (
        profile_urls({}, ["capture one at https://profiler.firefox.com/ please"]) == []
    )


def test_no_profile():
    assert profile_urls({"url": "https://example.com/"}, ["no links here"]) == []


def _att(id, name, **kw):
    return {"id": id, "file_name": name, "is_obsolete": 0, "is_private": 0, **kw}


def test_attached_profile_follows_linked_ones():
    # Bug 2086588: a screenshot, an about:memory report and a profile, all
    # but the screenshot application/gzip.
    atts = [
        _att(1, "Screenshot_20261028_150619.png"),
        _att(2, "memory-report.json.gz"),
        _att(3, "Firefox-2026-10-28-15.03-profile.json.gz"),
    ]
    assert profile_urls({}, ["https://share.firefox.dev/x1"], atts) == [
        "https://share.firefox.dev/x1",
        "https://bugzilla.mozilla.org/attachment.cgi?id=3",
    ]


def test_obsolete_and_private_profiles_skipped():
    atts = [
        _att(1, "profile.json.gz", is_obsolete=1),
        _att(2, "profile.json", is_private=1),
    ]
    assert profile_urls({}, [], atts) == []
