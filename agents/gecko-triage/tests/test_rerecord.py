"""Tests for the tool that removes a run's recorded actions so it can record them again."""

import pytest
from agent_tools.registry import ToolError
from conftest import (
    DUPE_TARGET,
    KNOWN_META,
    SHORT_NOTE,
    _comment,
    _recorder,
    _text,
    _update,
)
from hackbot_agents.gecko_triage import closing, hooks, rerecord
from hackbot_agents.gecko_triage.closing import (
    CloseVerdicts,
    record_close_verdict_tool,
    recorded_close,
    spawn_refusal,
)
from hackbot_agents.gecko_triage.rerecord import (
    MAX_REMOVALS,
    REMOVE_RECORDED_ACTIONS_TOOL,
    Removals,
    missing_comment_refusal,
    removal_caller_guard,
    removal_caller_refusal,
    remove_recorded_actions_tool,
)

INVALID = {"status": "RESOLVED", "resolution": "INVALID"}
DUPLICATE = {"status": "RESOLVED", "resolution": "DUPLICATE", "dupe_of": DUPE_TARGET}


async def _remove(rec, removals=None):
    return await remove_recorded_actions_tool(removals or Removals(rec))


async def _verdict(store, resolution="INVALID", verdict="confirm", dupe_of=None):
    await record_close_verdict_tool(
        store, resolution, verdict, "checked", dupe_of=dupe_of
    )


async def test_the_tool_removes_the_comment_and_every_field_change(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    await _verdict(store, "DUPLICATE", dupe_of=DUPE_TARGET)
    _comment(rec)
    _update(rec, DUPLICATE)
    _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    result = await _remove(rec)
    assert rec.actions == []
    assert result["removed"] == [
        "bugzilla.add_comment",
        "bugzilla.update_bug",
        "bugzilla.update_bug",
    ]
    assert result["removals_left"] == MAX_REMOVALS - 1


async def test_a_re_record_runs_every_comment_hook_again(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    await _remove(rec)
    with pytest.raises(ToolError, match="dash as an aside"):
        _comment(rec, text=_text("The decoder fails -- always."))
    with pytest.raises(ToolError, match="publicly"):
        _comment(rec, is_private=True)
    with pytest.raises(ToolError, match="links to"):
        _comment(rec, text=_text("See https://example.com/x."))
    assert rec.actions == []
    _comment(rec)
    with pytest.raises(ToolError, match="already recorded a comment"):
        _comment(rec)
    assert len(rec.actions) == 1


async def test_a_re_record_runs_every_update_hook_again(new_state):
    rec = _recorder(new_state)
    _comment(rec)
    _update(rec, {"severity": "S3", "priority": "P3"})
    await _remove(rec)
    with pytest.raises(ToolError, match="record the comment first"):
        _update(rec, {"severity": "S3", "priority": "P3"})
    _comment(rec)
    with pytest.raises(ToolError, match="never set S1"):
        _update(rec, {"severity": "S1"})
    _update(rec, {"severity": "S3", "priority": "P3"})
    with pytest.raises(ToolError, match="already recorded"):
        _update(rec, {"keywords": {"add": ["stalled"]}})
    assert [a["type"] for a in rec.actions] == [
        "bugzilla.add_comment",
        "bugzilla.update_bug",
    ]


async def test_a_duplicate_target_change_needs_the_duplicate_again(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    await _verdict(store, "DUPLICATE", dupe_of=DUPE_TARGET)
    _comment(rec)
    _update(rec, DUPLICATE)
    _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    await _remove(rec)
    _comment(rec)
    with pytest.raises(ToolError, match="after the DUPLICATE change is recorded"):
        _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    _update(rec, DUPLICATE)
    _update(rec, {"blocks": {"add": [KNOWN_META]}}, bug_id=DUPE_TARGET)
    assert len(rec.actions) == 3


async def test_the_run_may_remove_its_actions_twice_and_no_more(new_state):
    rec = _recorder(new_state)
    removals = Removals(rec)
    for _ in range(MAX_REMOVALS):
        _comment(rec)
        await _remove(rec, removals)
    _comment(rec)
    with pytest.raises(ToolError, match=f"{MAX_REMOVALS} times already"):
        await _remove(rec, removals)
    assert len(rec.actions) == 1
    assert removals.used == MAX_REMOVALS


async def test_a_call_with_nothing_recorded_is_refused_and_not_counted(new_state):
    rec = _recorder(new_state)
    removals = Removals(rec)
    with pytest.raises(ToolError, match="nothing is recorded"):
        await _remove(rec, removals)
    assert removals.used == 0


def test_the_comment_refusal_names_the_tool():
    rec = _recorder()
    _comment(rec)
    with pytest.raises(ToolError) as e:
        _comment(rec)
    assert REMOVE_RECORDED_ACTIONS_TOOL in str(e.value)
    assert "revising it" not in str(e.value)


def test_only_the_main_agent_may_remove_the_actions():
    assert removal_caller_refusal({"tool_name": REMOVE_RECORDED_ACTIONS_TOOL}) is None
    for data in (
        {"agent_type": "bug_investigator", "agent_id": "a1"},
        {"agent_type": closing.VERIFIER},
        {"agent_id": "a2"},
    ):
        assert removal_caller_refusal(data)


async def test_the_caller_guard_denies_a_subagent():
    denied = await removal_caller_guard({"agent_type": "investigator"}, None, None)
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert await removal_caller_guard({}, None, None) == {}


def test_the_tool_name_and_server():
    assert REMOVE_RECORDED_ACTIONS_TOOL == "mcp__rerecord__remove_recorded_actions"
    server = rerecord.removals_server(Removals(_recorder()))
    assert server["type"] == "sdk"


# --- the close verdict state ------------------------------------------------- #


async def test_a_confirm_before_the_first_comment_still_covers_a_re_record(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    await _verdict(store)
    _comment(rec)
    _update(rec, INVALID)
    await _remove(rec)
    _comment(rec)
    _update(rec, INVALID)
    assert recorded_close(rec.actions) == ("INVALID", None)
    assert store.outcome(("INVALID", None)) == "confirmed"
    assert store.warning(("INVALID", None)) is None


async def test_a_verdict_after_the_first_comment_stays_late(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    _comment(rec)
    await _verdict(store)
    await _remove(rec)
    assert store.comment_recorded
    _comment(rec)
    with pytest.raises(ToolError, match="after you recorded the comment"):
        _update(rec, INVALID)
    assert not store.confirms(("INVALID", None))
    assert store.warning(None) is not None


async def test_the_verifier_is_not_spawned_after_a_removal(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    _comment(rec)
    await _remove(rec)
    spawn = {"tool_input": {"subagent_type": closing.VERIFIER}}
    assert spawn_refusal(store, spawn)
    assert store.verifier_spawns == 0


async def test_a_refute_still_refuses_the_close_and_a_closing_comment(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    await _verdict(store, verdict="refute")
    _comment(rec)
    await _remove(rec)
    with pytest.raises(ToolError, match="refuted the close"):
        _comment(rec, text=_text("This bug is INVALID."))
    _comment(rec)
    with pytest.raises(ToolError, match="refuted closing this bug"):
        _update(rec, INVALID)
    assert store.outcome(None) == "refuted"


async def test_a_refused_close_still_warns_after_a_re_record_without_it(new_state):
    store = CloseVerdicts()
    rec = _recorder(new_state, close_verdicts=store)
    _comment(rec)
    with pytest.raises(ToolError):
        _update(rec, INVALID)
    await _remove(rec)
    _comment(rec, text=SHORT_NOTE)
    assert store.refused == [("INVALID", None)]
    assert "refused 1 close(s)" in store.warning(recorded_close(rec.actions))


def test_the_hooks_name_the_tool_in_their_docstring():
    assert "rerecord.py" in hooks.__doc__


@pytest.mark.parametrize("decision", ["needs_info", "analyzed", "invalid", None])
def test_a_removal_with_no_comment_again_fails_the_run(new_state, decision):
    rec = _recorder(new_state)
    _comment(rec)
    _update(rec, {"severity": "S3", "priority": "P3"})
    removals = Removals(rec, used=1)
    for action in rec.list_actions():
        rec.remove_action(action["action_id"])
    reason = missing_comment_refusal(removals, rec.list_actions(), decision)
    assert "did not record a comment again" in reason


def test_a_removal_with_a_comment_again_or_no_comment_needed_passes(new_state):
    rec = _recorder(new_state)
    assert missing_comment_refusal(Removals(rec, used=1), [], "watching") is None
    assert missing_comment_refusal(Removals(rec), [], "needs_info") is None
    _comment(rec)
    assert (
        missing_comment_refusal(Removals(rec, used=1), rec.list_actions(), "analyzed")
        is None
    )
