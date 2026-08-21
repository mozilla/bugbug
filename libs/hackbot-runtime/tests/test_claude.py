"""Tests for the shared claude-agent-sdk helpers (hackbot_runtime.claude)."""

import asyncio

import pytest
from claude_agent_sdk import (
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
)
from hackbot_runtime.claude import (
    Reporter,
    UnsettledResponseError,
    _truncate,
    receive_settled_response,
)


def test_truncate_short_string_unchanged():
    assert _truncate("hello", 10) == "hello"


def test_truncate_long_string_marks_remainder():
    out = _truncate("x" * 20, 5)
    assert out.startswith("xxxxx")
    assert "15 more chars" in out


def test_header_writes_banner_to_log(tmp_path):
    log = tmp_path / "agent.log"
    with Reporter(verbose=False, log_path=log) as reporter:
        reporter.header("bug 12345")
    contents = log.read_text()
    assert "# bug 12345" in contents
    assert "#" * 60 in contents


def test_header_always_prints_even_when_not_verbose(capsys):
    with Reporter(verbose=False, log_path=None) as reporter:
        reporter.header("bug 999")
    out = capsys.readouterr().out
    assert "# bug 999" in out


def test_no_log_file_when_path_is_none(tmp_path):
    # Should not raise and should not create any file.
    with Reporter(verbose=True, log_path=None) as reporter:
        reporter.header("section")
    assert not list(tmp_path.iterdir())


class _FakeClient:
    """Replays a fixed message list from ``receive_messages()``.

    If ``hang_after`` is set, blocks forever once the list is exhausted,
    simulating a connection left open with no further messages — the
    situation ``timeout_s`` in ``receive_settled_response`` is meant to
    bound.
    """

    def __init__(self, messages, hang_after: bool = False):
        self._messages = messages
        self._hang_after = hang_after

    async def receive_messages(self):
        for msg in self._messages:
            yield msg
        if self._hang_after:
            await asyncio.Event().wait()


def _result(is_error: bool = False, num_turns: int = 1) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=num_turns,
        session_id="s1",
    )


def _task_started(task_id: str, task_type: str = "local_agent") -> TaskStartedMessage:
    return TaskStartedMessage(
        subtype="task_started",
        data={},
        task_id=task_id,
        description="do a thing",
        uuid="u1",
        session_id="s1",
        task_type=task_type,
    )


def _task_notification(
    task_id: str, status: str = "completed"
) -> TaskNotificationMessage:
    return TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id=task_id,
        status=status,
        output_file="",
        summary="done",
        uuid="u2",
        session_id="s1",
    )


def _task_updated(task_id: str, status: str = "completed") -> TaskUpdatedMessage:
    return TaskUpdatedMessage(
        subtype="task_updated",
        data={},
        task_id=task_id,
        patch={"status": status},
        status=status,
    )


async def test_receive_settled_response_returns_immediately_when_nothing_pending():
    result = _result()
    client = _FakeClient([result])
    seen = []

    got = await receive_settled_response(client, on_message=seen.append)

    assert got is result
    assert seen == [result]


async def test_receive_settled_response_keeps_draining_past_early_result():
    started = _task_started("t1")
    early_result = _result(num_turns=1)
    notification = _task_notification("t1")
    final_result = _result(num_turns=2)
    client = _FakeClient([started, early_result, notification, final_result])

    got = await receive_settled_response(client)

    # The first ResultMessage arrived while "t1" was still open — it must be
    # ignored in favor of the one that follows the task's terminal message.
    assert got is final_result


async def test_receive_settled_response_task_updated_also_clears_pending():
    started = _task_started("t1")
    early_result = _result(num_turns=1)
    updated = _task_updated("t1")
    final_result = _result(num_turns=2)
    client = _FakeClient([started, early_result, updated, final_result])

    got = await receive_settled_response(client)

    assert got is final_result


async def test_receive_settled_response_ignores_non_deferring_task_types():
    # A backgrounded shell (task_type="local_bash") can run indefinitely by
    # design — the CLI itself never holds the result frame back for one, so
    # neither should we. Settling immediately (rather than waiting on "t1")
    # is the correct behavior here, not a race we need to rescue.
    started = _task_started("t1", task_type="local_bash")
    result = _result()
    client = _FakeClient([started, result])

    got = await receive_settled_response(client)

    assert got is result


async def test_receive_settled_response_raises_on_timeout_when_task_never_settles():
    client = _FakeClient([_task_started("t1"), _result()], hang_after=True)

    with pytest.raises(UnsettledResponseError):
        await receive_settled_response(client, timeout_s=0.05)


async def test_receive_settled_response_raises_when_stream_ends_without_result():
    client = _FakeClient([SystemMessage(subtype="init", data={})])

    with pytest.raises(UnsettledResponseError):
        await receive_settled_response(client)
