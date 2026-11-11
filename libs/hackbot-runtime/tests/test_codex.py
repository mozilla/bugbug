"""Tests for the shared Codex SDK Reporter (hackbot_runtime.codex)."""

from hackbot_runtime.codex import Reporter
from openai_codex.generated.v2_all import (
    ItemCompletedNotification,
    ItemStartedNotification,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
    TurnDiffUpdatedNotification,
    TurnStartedNotification,
)
from openai_codex.models import Notification, UnknownNotification


def _notification(method, model, data):
    data["threadId"] = "test-thread"
    data["turnId"] = "test-turn"
    return Notification(method, model.model_validate(data))


def _turn_started():
    turn = {"id": "test-turn", "items": [], "status": "inProgress"}
    return _notification("turn/started", TurnStartedNotification, {"turn": turn})


def _turn_completed(status="completed", error=None):
    turn = {"id": "test-turn", "items": [], "status": status, "durationMs": 1234}
    if error is not None:
        turn["error"] = {"message": error}
    return _notification("turn/completed", TurnCompletedNotification, {"turn": turn})


def _item(item):
    """An item's started and completed notifications, as a real stream sends them."""
    started = _notification(
        "item/started", ItemStartedNotification, {"item": item, "startedAtMs": 0}
    )
    completed = _notification(
        "item/completed", ItemCompletedNotification, {"item": item, "completedAtMs": 0}
    )
    return [started, completed]


def _usage(thread_total, input_tokens=100):
    """A usage update for one request, with the thread's running total."""
    last = {
        "inputTokens": input_tokens,
        "cachedInputTokens": 0,
        "outputTokens": 20,
        "reasoningOutputTokens": 0,
        "totalTokens": input_tokens + 20,
    }
    total = {
        "inputTokens": 0,
        "cachedInputTokens": 0,
        "outputTokens": 0,
        "reasoningOutputTokens": 0,
        "totalTokens": thread_total,
    }
    return _notification(
        "thread/tokenUsage/updated",
        ThreadTokenUsageUpdatedNotification,
        {"tokenUsage": {"last": last, "total": total}},
    )


def test_tool_call_and_result_are_logged(tmp_path):
    log = tmp_path / "agent.log"
    command = {
        "type": "commandExecution",
        "id": "c",
        "command": "cat test.js",
        "commandActions": [],
        "cwd": "/repo",
        "aggregatedOutput": "contents of test.js",
        "exitCode": 0,
        "status": "completed",
    }
    with Reporter(verbose=False, log_path=log) as reporter:
        for event in [_turn_started()] + _item(command):
            reporter.message(event)
    contents = log.read_text()
    assert "--- turn 1 ---" in contents
    assert '"command": "cat test.js"' in contents
    assert "[tool←ok]\ncontents of test.js" in contents


def test_result_counts_model_requests_not_repeated_usage():
    # Two requests. In between, Codex repeats the same total (as when rate limits
    # change) and reports the context window filling (a higher total, no input).
    message = {"type": "agentMessage", "id": "m", "text": "Fixed it.", "phase": None}
    events = (
        [_turn_started(), _usage(120), _usage(120), _usage(500, input_tokens=0)]
        + _item(message)
        + [_usage(620), _turn_completed()]
    )
    with Reporter(verbose=False, log_path=None) as reporter:
        for event in events:
            reporter.message(event)
    assert not reporter.result.is_error
    assert reporter.result.result == "Fixed it."
    assert reporter.result.num_turns == 2
    assert reporter.result.duration_ms == 1234


def test_done_line_always_prints_even_when_not_verbose(capsys):
    events = [_turn_started(), _usage(120), _turn_completed()]
    with Reporter(verbose=False, log_path=None) as reporter:
        for event in events:
            reporter.message(event)
    out = capsys.readouterr().out
    assert "[done] turns=1 tokens=in:100(cached:0),out:20(reasoning:0)" in out


def test_failed_turn_is_an_error(capsys):
    events = [_turn_started(), _turn_completed("failed", error="test failure")]
    with Reporter(verbose=False, log_path=None) as reporter:
        for event in events:
            reporter.message(event)
    assert reporter.result.is_error
    assert reporter.result.error == "test failure"
    assert "[done] ERROR: test failure" in capsys.readouterr().out


def test_unknown_notifications_are_logged_but_streaming_updates_are_not(tmp_path):
    log = tmp_path / "agent.log"
    delta = UnknownNotification({"delta": "streamed text"})
    diff = {"diff": "+big diff"}
    events = [
        Notification("test/unknownMethod", UnknownNotification({"value": 1})),
        Notification("item/agentMessage/delta", delta),
        _notification("turn/diff/updated", TurnDiffUpdatedNotification, diff),
    ]
    with Reporter(verbose=False, log_path=log) as reporter:
        for event in events:
            reporter.message(event)
    contents = log.read_text()
    assert '[system:test/unknownMethod] {"value": 1}' in contents
    assert "streamed text" not in contents
    assert "big diff" not in contents
