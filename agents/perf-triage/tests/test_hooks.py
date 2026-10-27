from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from agent_tools.registry import ToolError
from hackbot_agents.perf_triage.hooks import (
    REVIEW_FLAGS,
    add_comment_hook,
    update_bug_hook,
)
from hackbot_runtime.actions import ActionsRecorder

BUG = 1839859


def recorder(mode="full") -> ActionsRecorder:
    r = ActionsRecorder(artifacts_dir=Path(tempfile.mkdtemp()))
    r.add_hook("bugzilla.add_comment", add_comment_hook(r, BUG))
    r.add_hook("bugzilla.update_bug", update_bug_hook(r, BUG, mode))
    return r


def update(r, changes, bug_id=BUG):
    return r.record("bugzilla.update_bug", {"bug_id": bug_id, "changes": changes})


def comment(r, text="rated low", bug_id=BUG, is_private=False):
    return r.record(
        "bugzilla.add_comment",
        {"bug_id": bug_id, "text": text, "is_private": is_private},
    )


def refused(fn):
    with pytest.raises(ToolError):
        fn()


def test_full_write_allowed():
    r = recorder()
    comment(r)
    update(
        r,
        {"cf_performance_impact": "low", "keywords": {"add": ["perf:responsiveness"]}},
    )
    assert [a["type"] for a in r.actions] == [
        "bugzilla.add_comment",
        "bugzilla.update_bug",
    ]


def test_reviewer_needinfo_allowed_on_high():
    r = recorder()
    update(r, {"cf_performance_impact": "high", "flags": REVIEW_FLAGS})
    assert len(r.actions) == 1


def test_top50_allowed():
    r = recorder()
    update(r, {"keywords": {"add": ["top50"]}})
    assert len(r.actions) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"keywords": {"remove": ["perf"]}},
        {"keywords": {"set": ["perf:pageload"]}},
        {"keywords": {"add": ["perf:pageload"], "remove": ["perf"]}},
        {"keywords": {"add": ["regression"]}},
        {"keywords": ["perf:pageload"]},
        {"severity": "S2"},
        {"component": "Layout"},
        {"cf_performance_impact": "?"},
        {"cf_performance_impact": "critical"},
        {"cf_performance_impact": "medium", "flags": REVIEW_FLAGS},
        {
            "cf_performance_impact": "high",
            "flags": [{"name": "needinfo", "status": "?", "requestee": "x@y.z"}],
        },
        {},
    ],
)
def test_out_of_bounds_changes_refused(changes):
    r = recorder()
    refused(lambda: update(r, changes))
    assert r.actions == []


def test_other_bug_refused():
    r = recorder()
    refused(lambda: update(r, {"cf_performance_impact": "low"}, bug_id=1))
    refused(lambda: comment(r, bug_id=1))
    assert r.actions == []


def test_update_refused_in_comment_only():
    r = recorder("comment_only")
    refused(lambda: update(r, {"cf_performance_impact": "low"}))
    comment(r)
    assert [a["type"] for a in r.actions] == ["bugzilla.add_comment"]


def test_one_of_each():
    r = recorder()
    comment(r)
    update(r, {"cf_performance_impact": "low"})
    refused(lambda: comment(r))
    refused(lambda: update(r, {"cf_performance_impact": "high"}))
    assert len(r.actions) == 2


def test_private_or_empty_comment_refused():
    r = recorder()
    refused(lambda: comment(r, is_private=True))
    refused(lambda: comment(r, text="  "))
    assert r.actions == []
