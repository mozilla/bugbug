"""Tests for the shared claude-agent-sdk Reporter (hackbot_runtime.claude)."""

from claude_agent_sdk import ResultMessage, SystemMessage
from hackbot_runtime.claude import Reporter, _truncate


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


def _tasks_changed(tasks):
    return SystemMessage(
        subtype="background_tasks_changed",
        data={"type": "system", "subtype": "background_tasks_changed", "tasks": tasks},
    )


def _subagent(name):
    return {"task_id": f"a-{name}", "task_type": "local_agent", "subagent_type": name}


def _result():
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        result="done",
    )


def test_reports_subagents_still_running_at_the_end(capsys):
    with Reporter(verbose=False, log_path=None) as reporter:
        reporter.message(_tasks_changed([_subagent("investigator")]))
        reporter.message(_result())

    assert reporter.unfinished_subagents == ["investigator"]
    assert "subagents still running: investigator" in capsys.readouterr().out


def test_a_subagent_that_finished_is_not_unfinished():
    with Reporter(verbose=False, log_path=None) as reporter:
        reporter.message(_tasks_changed([_subagent("duplicate_hunter")]))
        reporter.message(_tasks_changed([]))
        reporter.message(_result())

    assert reporter.unfinished_subagents == []


def test_background_shell_tasks_are_not_subagents():
    with Reporter(verbose=False, log_path=None) as reporter:
        reporter.message(_tasks_changed([{"task_id": "b1", "task_type": "local_bash"}]))

    assert reporter.unfinished_subagents == []
