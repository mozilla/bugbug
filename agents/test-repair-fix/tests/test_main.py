import json

import pytest
from hackbot_agents.test_repair.resolve import CommitRange, FailingGroup, Investigation
from hackbot_agents.test_repair_fix.__main__ import _load_analysis
from hackbot_runtime import AgentError

INVESTIGATION = Investigation(
    project="autoland",
    hg_revision="hgrev",
    harness="xpcshell",
    platform="linux1804-64/opt",
    failing_groups=[FailingGroup("a/xpcshell.toml", ["a/test_a.js"])],
    commit_range=CommitRange(head="f" * 40, span=2),
)


def _parent(tmp_path, culprit):
    findings = {
        "classification": "regression",
        "recommendation": "backout" if culprit else "do_not_backout",
        "culprit_commit": culprit,
        "num_turns": 9,
    }
    (tmp_path / "summary.json").write_text(
        json.dumps({"status": "ok", "findings": findings, "actions": []})
    )
    (tmp_path / "investigation.json").write_text(
        json.dumps({"task_id": "T1", "investigation": INVESTIGATION.to_dict()})
    )
    return tmp_path


def test_the_fix_starts_from_the_analysis_runs_verdict(tmp_path):
    analysis, task_id, investigation = _load_analysis(_parent(tmp_path, "c" * 40))
    assert analysis.culprit_commit == "c" * 40
    assert task_id == "T1"
    assert investigation == INVESTIGATION


def test_a_parent_without_a_culprit_fails_the_run(tmp_path):
    with pytest.raises(AgentError, match="blamed no commit"):
        _load_analysis(_parent(tmp_path, None))
