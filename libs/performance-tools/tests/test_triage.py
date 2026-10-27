from __future__ import annotations

from performance_tools.inputs import BugIdInput
from performance_tools.tasks import RunTracker, TaskConfig
from performance_tools.triage import PerfTriage, ProfileFinding, TriageResult


def _server():
    return {"type": "http", "url": "http://broker.invalid/mcp"}


def make_triage(**kw) -> PerfTriage:
    return PerfTriage(
        TaskConfig(), RunTracker(), BugIdInput(bug_id=1839859), _server(), **kw
    )


def test_skipping_the_profile_loads_only_the_triage_skill():
    opts = make_triage(analyse_profile=False).agent_options()
    assert opts.env["PERF_TRIAGE_SKIP_PROFILE"] == "1"
    assert opts.skills == ["triage-perf-bug"]


def test_skip_is_the_default():
    assert make_triage().agent_options().env["PERF_TRIAGE_SKIP_PROFILE"] == "1"


def test_perf_triage_loads_profiler_analysis():
    t = make_triage(analyse_profile=True, profile_urls=["https://share.firefox.dev/x"])
    opts = t.agent_options()
    assert "PERF_TRIAGE_SKIP_PROFILE" not in opts.env
    assert opts.env["PERF_TRIAGE_AGENT_MODE"] == "1"
    assert opts.skills == ["triage-perf-bug", "profiler-analysis"]
    assert "https://share.firefox.dev/x" in t.user_prompt()


def test_no_profile_urls_in_prompt_when_skipping():
    assert "share.firefox.dev" not in make_triage().user_prompt()


def test_result_carries_unestablished_and_profile():
    r = TriageResult(
        impact="low",
        score=7.2,
        comment="c",
        unestablished=["pages-affected"],
        profile=ProfileFinding(
            url="u",
            analysed=False,
            not_analysed_reason="404",
            changed_attributes={},
            component=None,
            summary=None,
        ),
    )
    assert r.unestablished == ["pages-affected"]
    assert r.profile.not_analysed_reason == "404"


def test_old_shape_still_parses():
    r = TriageResult(impact="low", score=7.2, comment="c")
    assert r.unestablished == [] and r.profile is None


def test_prompt_maps_the_new_keys():
    from performance_tools.triage import TRIAGE_TASK_DETAILS

    assert "`unestablished`" in TRIAGE_TASK_DETAILS
    assert "`profile`" in TRIAGE_TASK_DETAILS
    assert "`related`" in TRIAGE_TASK_DETAILS


def test_related_defaults_empty_and_parses():
    base = dict(impact="low", score=1.0, comment="c")
    assert TriageResult(**base).related == []
    r = TriageResult(**base, related=[{"bug": 2084175, "settled": ["platforms"]}])
    assert r.related[0].bug == 2084175 and r.related[0].settled == ["platforms"]
