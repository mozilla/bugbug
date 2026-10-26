"""Tests for the question to the reporter that a run posts whatever its confidence.

hackbot-api applies `bugzilla.request_info` even when the rest of the run is held, so
what gets recorded here is what reaches the bug unattended.
"""

from hackbot_agents.frontend_triage.agent import (
    MAX_REPORTER_QUESTION_CHARS,
    FrontendTriageResult,
    parse_plan,
    record_reporter_question,
)
from hackbot_runtime.actions import ActionsRecorder

BUG_ID = 1968342
REPORTER = "reporter@example.com"
QUESTION = "Does this still happen in a new profile?"


def _result(question: str | None = QUESTION) -> FrontendTriageResult:
    return FrontendTriageResult(
        bug_id=BUG_ID, num_turns=1, total_cost_usd=0.0, reporter_question=question
    )


def test_parse_plan_reads_the_reporter_question():
    def plan(body: str) -> dict:
        return parse_plan(f"```json\n{body}\n```")

    assert plan(f'{{"reporter_question": " {QUESTION} "}}')["reporter_question"] == (
        QUESTION
    )
    for body in (
        '{"reporter_question": null}',
        '{"reporter_question": "  "}',
        '{"reporter_question": ["a list"]}',
        "{}",
    ):
        assert plan(body)["reporter_question"] is None, body


def test_the_question_needinfos_the_reporter():
    recorder = ActionsRecorder()
    record_reporter_question(recorder, _result(), f" {REPORTER} ")

    [action] = recorder.actions
    assert action["type"] == "bugzilla.request_info"
    assert action["params"]["bug_id"] == BUG_ID
    assert action["params"]["requestee"] == REPORTER
    assert action["params"]["text"].startswith(QUESTION + "\n\n---\n\n")


def test_nothing_is_recorded_without_a_question_or_a_reporter():
    for result, reporter in (
        (_result(None), REPORTER),
        (_result(), None),
        (_result(), ""),
        (_result(), {"email": REPORTER}),
    ):
        recorder = ActionsRecorder()
        assert record_reporter_question(recorder, result, reporter) is None
        assert recorder.actions == []


def test_an_overlong_question_is_dropped_rather_than_cut_off():
    recorder = ActionsRecorder()
    question = "x" * (MAX_REPORTER_QUESTION_CHARS + 1)
    assert record_reporter_question(recorder, _result(question), REPORTER) is None
    assert recorder.actions == []
