"""Tests for the record-time limits on what this agent may write to a bug.

The hooks run inside the agent as the action is recorded, so a rejection reaches the
model as a tool error and the action never lands in summary.json — see
hackbot_agents/frontend_triage/hooks.py.
"""

import pytest
from agent_tools.registry import ToolError
from hackbot_agents.frontend_triage.hooks import add_comment_hook, update_bug_hook
from hackbot_runtime.actions import ActionsRecorder

BUG = 2014702


def _recorder():
    rec = ActionsRecorder()
    rec.add_hook("bugzilla.update_bug", update_bug_hook(rec, BUG))
    rec.add_hook("bugzilla.add_comment", add_comment_hook(rec, BUG))
    return rec


def _update(rec, changes, bug_id=BUG):
    return rec.record(
        "bugzilla.update_bug",
        {"bug_id": bug_id, "changes": changes},
        reasoning="rule X",
    )


def _comment(rec, bug_id=BUG, **params):
    return rec.record(
        "bugzilla.add_comment",
        {"bug_id": bug_id, "text": "hi", **params},
        reasoning="rule X",
    )


def _rejects(changes, bug_id=BUG):
    """Assert the change is refused and nothing is left behind."""
    rec = _recorder()
    with pytest.raises(ToolError):
        _update(rec, changes, bug_id=bug_id)
    assert rec.actions == []


def test_adding_an_allowed_keyword_is_recorded():
    rec = _recorder()
    _update(rec, {"keywords": {"add": ["perf"]}})
    assert rec.actions[0]["params"]["changes"] == {"keywords": {"add": ["perf"]}}


def test_setting_a_current_severity_is_recorded():
    rec = _recorder()
    _update(rec, {"severity": "S2"})
    assert rec.actions[0]["params"]["changes"] == {"severity": "S2"}


def test_an_unknown_severity_is_refused():
    # Anything `rules/severity-assessment.md` doesn't define, including the legacy
    # words Bugzilla still accepts for old bugs and the unset `--`/`N/A`.
    for value in (
        "S5",
        "critical",
        "normal",
        "enhancement",
        "--",
        "N/A",
        "",
        "s2",
        "S2 ",
    ):
        _rejects({"severity": value})


def test_a_severity_that_is_not_a_string_is_refused():
    for value in (None, True, 2, ["S2"], {"set": "S2"}):
        _rejects({"severity": value})


def test_an_unknown_keyword_is_refused():
    # `keywords` being an allowed field does not open up all ~340 keywords Bugzilla
    # defines, several of which drive automation.
    for keyword in ("checkin-needed", "sec-high", "leave-open", "meta", ""):
        _rejects({"keywords": {"add": [keyword]}})


def test_one_bad_keyword_in_a_batch_is_enough():
    _rejects({"keywords": {"add": ["perf", "leave-open"]}})


def test_a_keyword_that_is_not_a_string_is_refused():
    # Unhashable entries included: a bare `in` against a frozenset would raise
    # TypeError here rather than returning a refusal the agent can read.
    for keyword in (None, 7, True, ["perf"], {"name": "perf"}):
        _rejects({"keywords": {"add": [keyword]}})


def test_a_destructive_keyword_edit_is_refused():
    # A bare list replaces every keyword already on the bug, and the recording tool
    # advertises add/remove/set to the model, so name-only checking is not enough.
    for value in (
        ["perf"],
        "perf",
        {"set": ["perf"]},
        {"set": []},
        {"remove": ["regression"]},
        {"add": ["perf"], "remove": ["regression"]},
        {"add": "perf"},
        {"add": []},
        {},
    ):
        _rejects({"keywords": value})


def test_a_field_outside_the_allowlist_is_refused():
    # `bugzilla.update_bug` accepts any field the REST endpoint does, so the
    # allowlist is what keeps triage from resolving or reassigning the bug.
    for changes in (
        {"status": "RESOLVED"},
        {"resolution": "DUPLICATE"},
        {"assigned_to": "someone@mozilla.com"},
        {"component": "General"},
        {"comment": "hi"},  # a third route to posting a comment
        {"keywords": {"add": ["perf"]}, "status": "RESOLVED"},  # one bad key is enough
    ):
        _rejects(changes)


def test_changes_must_be_a_non_empty_mapping():
    for changes in ([], "", 0, {}, None, "keywords=perf"):
        _rejects(changes)


def test_a_change_against_another_bug_is_refused():
    # The bug being triaged can name any other bug in its comments, and the agent
    # reads all of them.
    for bug_id in (999, "2014702", None, f"{BUG} "):
        _rejects({"severity": "S2"}, bug_id=bug_id)


def test_a_private_comment_is_refused():
    rec = _recorder()
    with pytest.raises(ToolError):
        _comment(rec, is_private=True)
    assert rec.actions == []


def test_a_public_comment_is_recorded():
    rec = _recorder()
    _comment(rec, is_private=False)
    assert rec.actions[0]["type"] == "bugzilla.add_comment"


def test_a_comment_against_another_bug_is_refused():
    for bug_id in (999, "2014702", None, f"{BUG} "):
        rec = _recorder()
        with pytest.raises(ToolError):
            _comment(rec, bug_id=bug_id)
        assert rec.actions == []


def test_only_one_comment_may_be_recorded():
    # A model told to write "a single brief comment" can still record fifty; the
    # prompt is not what caps this.
    rec = _recorder()
    _comment(rec)
    with pytest.raises(ToolError):
        _comment(rec)
    assert len(rec.actions) == 1


def test_only_one_field_change_may_be_recorded():
    rec = _recorder()
    _update(rec, {"severity": "S2"})
    with pytest.raises(ToolError):
        _update(rec, {"severity": "S3"})
    assert len(rec.actions) == 1


def test_a_comment_and_a_field_change_coexist():
    # One plan comment plus one field change is the pair the rules sanction, and the
    # apply step coalesces it into a single Bugzilla PUT.
    rec = _recorder()
    _comment(rec)
    _update(rec, {"keywords": {"add": ["perf"]}})
    assert [a["type"] for a in rec.actions] == [
        "bugzilla.add_comment",
        "bugzilla.update_bug",
    ]


def test_a_refused_action_does_not_use_up_the_allowance():
    # The count is taken from what was actually recorded, so a rejected attempt
    # leaves the agent free to retry with a corrected call.
    rec = _recorder()
    with pytest.raises(ToolError):
        _update(rec, {"severity": "critical"})
    _update(rec, {"severity": "S2"})
    assert len(rec.actions) == 1
