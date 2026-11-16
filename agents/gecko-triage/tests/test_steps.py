"""Tests for the step counts of each session of a run."""

from hackbot_agents.gecko_triage.steps import (
    Steps,
    call_counter,
    limit_hit_hook,
    note_call,
    note_limit_hit,
)

NOTE = (
    "Profile read.\n  NOTE: this agent stopped at its 30-turn limit before finishing. "
    "It was still calling tools and had produced no report."
)


def test_each_spawn_is_its_own_session():
    steps = Steps()
    for data in (
        {"agent_id": "a1", "agent_type": "profile_analyst"},
        {},
        {"agent_id": "a2", "agent_type": "profile_analyst"},
        {"agent_id": "a1", "agent_type": "profile_analyst"},
        {},
    ):
        note_call(steps, data)
    assert steps.report() == [
        {"session": "profile_analyst", "tool_calls": 2},
        {"session": "main", "tool_calls": 2},
        {"session": "profile_analyst", "tool_calls": 1},
    ]


def test_a_spawn_with_no_type_still_counts():
    steps = Steps()
    note_call(steps, {"agent_id": "a1"})
    assert steps.report() == [{"session": "subagent", "tool_calls": 1}]


def test_a_limit_note_in_a_spawn_result_is_a_hit():
    steps = Steps()
    for response in (NOTE, [{"type": "text", "text": NOTE}], {"content": [NOTE]}):
        note_limit_hit(
            steps,
            {
                "tool_input": {"subagent_type": "profile_analyst"},
                "tool_response": response,
            },
        )
    assert steps.limit_hits == [{"session": "profile_analyst", "limit": 30}] * 3


def test_a_spawn_result_with_no_note_is_no_hit():
    steps = Steps()
    note_limit_hit(steps, {"tool_input": {}, "tool_response": "A full report."})
    note_limit_hit(steps, {"tool_input": {}, "tool_response": None})
    assert steps.limit_hits == []


async def test_the_hooks_answer_as_hooks():
    steps = Steps()
    assert await call_counter(steps)({}, "toolu_1", None) == {}
    data = {"tool_input": {"subagent_type": "log_analyst"}, "tool_response": NOTE}
    assert await limit_hit_hook(steps)(data, "toolu_2", None) == {}
    assert steps.report() == [{"session": "main", "tool_calls": 1}]
    assert steps.limit_hits == [{"session": "log_analyst", "limit": 30}]
