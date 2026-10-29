"""Tests for the independent check of a closing decision: the verdict tool and store."""

import json

import pytest
from agent_tools.registry import ToolError
from hackbot_agents.gecko_triage import closing
from hackbot_agents.gecko_triage.closing import (
    RECORD_CLOSE_VERDICT_TOOL,
    VERIFIER,
    CloseVerdict,
    CloseVerdicts,
    close_verdict_refusal,
    comment_marker_hook,
    record_close_verdict_tool,
    recorded_close,
    spawn_refusal,
    verdict_caller_guard,
    verdict_caller_refusal,
)


async def _record(store, **kwargs):
    args = {
        "resolution": "INVALID",
        "dupe_of": None,
        "verdict": "confirm",
        "reasons": "The quoted code is in the tree and the claim is wrong.",
        "corrections": [],
        **kwargs,
    }
    return await record_close_verdict_tool(store, **args)


async def test_the_tool_stores_the_verdict():
    store = CloseVerdicts()
    result = await _record(store, corrections=["Do not cite AssertedCast as a guard."])
    assert store.verdicts == [
        CloseVerdict(
            resolution="INVALID",
            dupe_of=None,
            verdict="confirm",
            reasons="The quoted code is in the tree and the claim is wrong.",
            corrections=("Do not cite AssertedCast as a guard.",),
        )
    ]
    assert result["recorded"] == "confirm"
    assert result["corrections"] == ["Do not cite AssertedCast as a guard."]


async def test_the_tool_normalizes_the_resolution_and_the_verdict():
    store = CloseVerdicts()
    await _record(store, resolution=" duplicate ", dupe_of=9000004, verdict="Refute")
    (verdict,) = store.verdicts
    assert (verdict.resolution, verdict.dupe_of, verdict.verdict) == (
        "DUPLICATE",
        9000004,
        "refute",
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"resolution": "WORKSFORME"},
        {"resolution": 3},
        {"verdict": "maybe"},
        {"reasons": "  "},
        {"reasons": None},
        {"corrections": "one string"},
        {"corrections": [1]},
        {"resolution": "DUPLICATE"},
        {"resolution": "DUPLICATE", "dupe_of": 0},
        {"resolution": "DUPLICATE", "dupe_of": "9000004"},
        {"resolution": "DUPLICATE", "dupe_of": True},
        {"resolution": "INVALID", "dupe_of": 9000004},
    ],
)
async def test_the_tool_refuses_a_bad_verdict_and_stores_nothing(kwargs):
    store = CloseVerdicts()
    with pytest.raises(ToolError):
        await _record(store, **kwargs)
    assert store.verdicts == []


async def test_blank_corrections_are_dropped():
    store = CloseVerdicts()
    await _record(store, corrections=["  ", "Say the code is not in the tree."])
    assert store.verdicts[0].corrections == ("Say the code is not in the tree.",)


def _store(*verdicts):
    return CloseVerdicts([CloseVerdict(r, d, v, "reasons", ()) for r, d, v in verdicts])


def test_a_close_with_no_verdict_is_refused():
    reason = close_verdict_refusal(CloseVerdicts(), "INVALID", None)
    assert "close_verifier" in reason


def test_a_close_after_a_refute_is_refused():
    store = _store(("INVALID", None, "refute"))
    reason = close_verdict_refusal(store, "INVALID", None)
    assert "refuted" in reason and "Analyzed or Needs info" in reason


def test_a_refute_rules_out_every_close_in_the_run():
    store = _store(("INVALID", None, "refute"), ("WONTFIX", None, "confirm"))
    assert close_verdict_refusal(store, "WONTFIX", None) is not None
    store = _store(("INVALID", None, "confirm"), ("INVALID", None, "refute"))
    assert close_verdict_refusal(store, "INVALID", None) is not None


def test_a_confirm_for_another_resolution_is_refused():
    store = _store(("INVALID", None, "confirm"))
    assert "RESOLVED WONTFIX" in close_verdict_refusal(store, "WONTFIX", None)


def test_a_confirm_for_another_dupe_of_is_refused():
    store = _store(("DUPLICATE", 9000004, "confirm"))
    reason = close_verdict_refusal(store, "DUPLICATE", 9000005)
    assert "dupe_of 9000005" in reason


def test_a_matching_confirm_passes():
    store = _store(("INVALID", None, "confirm"))
    assert close_verdict_refusal(store, "INVALID", None) is None
    store = _store(("DUPLICATE", 9000004, "confirm"))
    assert close_verdict_refusal(store, "DUPLICATE", 9000004) is None


@pytest.mark.parametrize(
    ("verdicts", "close", "outcome"),
    [
        ((), None, None),
        ((), ("INVALID", None), None),
        ((("INVALID", None, "confirm"),), ("INVALID", None), "confirmed"),
        ((("INVALID", None, "confirm"),), None, None),
        ((("WONTFIX", None, "confirm"),), ("INVALID", None), None),
        ((("DUPLICATE", 9000004, "confirm"),), ("DUPLICATE", 9000005), None),
        ((("INVALID", None, "refute"),), None, "refuted"),
        (
            (("INVALID", None, "confirm"), ("INVALID", None, "refute")),
            ("INVALID", None),
            "refuted",
        ),
    ],
)
def test_the_outcome_names_a_confirm_only_for_the_recorded_close(
    verdicts, close, outcome
):
    assert _store(*verdicts).outcome(close) == outcome


async def test_a_verdict_after_the_comment_does_not_confirm_the_close():
    store = CloseVerdicts()
    comment_marker_hook(store)({"type": "bugzilla.add_comment"})
    await _record(store)
    assert store.verdicts[0].before_comment is False
    reason = close_verdict_refusal(store, "INVALID", None)
    assert "after you recorded the comment" in reason
    assert "Spawn close_verifier before you record the comment" in reason
    assert store.outcome(("INVALID", None)) is None


async def test_a_verdict_before_the_comment_confirms_the_close():
    store = CloseVerdicts()
    await _record(store)
    comment_marker_hook(store)({"type": "bugzilla.add_comment"})
    assert close_verdict_refusal(store, "INVALID", None) is None


def test_the_refusal_says_to_verify_before_the_comment():
    reason = close_verdict_refusal(CloseVerdicts(), "INVALID", None)
    assert "Spawn close_verifier before you record the comment" in reason


def _spawn(subagent_type):
    return {"tool_name": "Agent", "tool_input": {"subagent_type": subagent_type}}


def test_close_verifier_is_spawned_once_per_run():
    store = CloseVerdicts()
    assert spawn_refusal(store, _spawn(VERIFIER)) is None
    reason = spawn_refusal(store, _spawn(VERIFIER))
    assert "once per run" in reason
    assert spawn_refusal(store, _spawn("bug_investigator")) is None
    assert spawn_refusal(store, {"tool_name": "Agent"}) is None
    assert store.verifier_spawns == 1


def test_a_verifier_that_records_no_verdict_leaves_the_close_refused():
    store = CloseVerdicts()
    assert spawn_refusal(store, _spawn(VERIFIER)) is None
    assert spawn_refusal(store, _spawn(VERIFIER)) is not None
    assert close_verdict_refusal(store, "INVALID", None) is not None


def _update(**changes):
    return {"type": "bugzilla.update_bug", "params": {"bug_id": 1, "changes": changes}}


def test_the_recorded_close_is_read_from_the_actions():
    comment = {"type": "bugzilla.add_comment", "params": {"bug_id": 1}}
    assert recorded_close([]) is None
    assert recorded_close([comment, _update(severity="S3")]) is None
    assert recorded_close([comment, _update(component="Graphics")]) is None
    assert recorded_close(
        [comment, _update(status="RESOLVED", resolution="INVALID")]
    ) == ("INVALID", None)
    dupe = _update(status="RESOLVED", resolution="DUPLICATE", dupe_of=9000004)
    assert recorded_close([comment, dupe]) == ("DUPLICATE", 9000004)


def test_only_the_close_verifier_may_record_a_verdict():
    assert verdict_caller_refusal({"agent_type": VERIFIER, "agent_id": "a1"}) is None
    for data in ({}, {"agent_type": "duplicate_hunter", "agent_id": "a2"}):
        assert VERIFIER in verdict_caller_refusal(data)


async def test_the_caller_guard_denies_the_main_agent():
    denied = await verdict_caller_guard(
        {"tool_name": RECORD_CLOSE_VERDICT_TOOL}, None, None
    )
    assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert await verdict_caller_guard({"agent_type": VERIFIER}, None, None) == {}


def test_the_verdict_tool_name_and_server():
    assert RECORD_CLOSE_VERDICT_TOOL == "mcp__verdicts__record_close_verdict"
    server = closing.verdicts_server(CloseVerdicts())
    assert server["type"] == "sdk" and server["name"] == "verdicts"


def test_close_verifier_is_not_spawned_after_the_comment():
    store = CloseVerdicts()
    comment_marker_hook(store)({"type": "bugzilla.add_comment"})
    reason = spawn_refusal(store, _spawn(VERIFIER))
    assert "must run before the comment" in reason
    assert "The bug stays open in this run" in reason
    assert (store.verifier_spawns, store.late_spawns) == (0, 1)
    assert "after it recorded the comment" in store.warning(None)
    assert "after it recorded the comment" in store.warning(("INVALID", None))
    assert spawn_refusal(store, _spawn("log_analyst")) is None


def test_the_store_warns_on_a_refused_close_unless_a_retry_recorded_it():
    assert CloseVerdicts().warning(None) is None
    store = CloseVerdicts(refused=[("INVALID", None), (None, None)])
    assert "refused 2 close(s)" in store.warning(None)
    assert store.warning(("INVALID", None)) is None
    assert CloseVerdicts(refused=[("invalid", None)]).warning(("INVALID", None)) is None
    assert (
        CloseVerdicts(refused=[("DUPLICATE", None)]).warning(("DUPLICATE", 7)) is None
    )
    assert "refused 1 close(s)" in store.warning(("WONTFIX", None))


def test_a_refused_duplicate_of_another_bug_keeps_the_warning():
    store = CloseVerdicts(refused=[("DUPLICATE", 9000004)])
    assert "refused 1 close(s)" in store.warning(("DUPLICATE", 9000005))
    assert store.warning(("DUPLICATE", 9000004)) is None


MCP_SPILL = (
    "Error: result (912,334 characters) exceeds maximum allowed tokens. Output has "
    "been saved to /home/u/.claude/projects/-r/s/tool-results/mcp-x-y-1.txt.\n"
)
PERSISTED = [
    {
        "type": "text",
        "text": "<persisted-output>\nOutput too large (67KB). Full output saved to: /p",
    }
]


def _post(response, agent_type=VERIFIER):
    return {
        "hook_event_name": "PostToolUse",
        "agent_type": agent_type,
        "tool_name": "mcp__searchfox__get_file",
        "tool_response": response,
    }


@pytest.mark.parametrize(
    "response",
    [
        MCP_SPILL,
        PERSISTED,
        PERSISTED[0]["text"],
        {"content": PERSISTED},
        [{"type": "text", "text": MCP_SPILL}],
    ],
)
def test_a_verifier_spill_sets_the_flag(response):
    store = CloseVerdicts()
    closing.note_verifier_spill(store, _post(response))
    assert store.verifier_spilled


def test_a_normal_result_or_the_main_agents_spill_sets_no_flag():
    store = CloseVerdicts()
    closing.note_verifier_spill(store, _post({"bug_id": 1, "comments": []}))
    closing.note_verifier_spill(store, _post(MCP_SPILL, agent_type=None))
    closing.note_verifier_spill(store, _post(MCP_SPILL, agent_type="bug_investigator"))
    assert not store.verifier_spilled


QUOTING_BUG = {
    "bug_id": 1,
    "comments": [
        {"text": "The log said <persisted-output> and Full output saved to: /tmp/x"},
        {"text": MCP_SPILL},
    ],
}


@pytest.mark.parametrize(
    "response",
    [
        QUOTING_BUG,
        [{"type": "text", "text": json.dumps(QUOTING_BUG)}],
        {"content": [{"type": "text", "text": json.dumps(QUOTING_BUG)}]},
        "A comment quoting <persisted-output>",
        "Error: result (5 characters) is fine",
    ],
)
def test_bug_text_that_quotes_a_spill_notice_sets_no_flag(response):
    store = CloseVerdicts()
    closing.note_verifier_spill(store, _post(response))
    assert not store.verifier_spilled


async def test_a_spill_notice_then_a_confirm_is_refused():
    store = CloseVerdicts()
    await closing.spill_hook(store)(_post(MCP_SPILL), "toolu_1", None)
    with pytest.raises(ToolError) as e:
        await _record(store)
    assert "a tool result was too large to read; refute instead" in str(e.value)
    assert store.verdicts == []


async def test_a_spill_notice_then_a_refute_is_accepted():
    store = CloseVerdicts()
    await closing.spill_hook(store)(_post(MCP_SPILL), "toolu_1", None)
    await _record(
        store,
        verdict="refute",
        reasons="A tool result was too large to read, so the close cannot be verified.",
    )
    assert [v.verdict for v in store.verdicts] == ["refute"]
