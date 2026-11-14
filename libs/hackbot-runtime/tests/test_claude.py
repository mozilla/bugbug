"""Tests for the shared claude-agent-sdk Reporter (hackbot_runtime.claude)."""

from claude_agent_sdk import SystemMessage
from hackbot_runtime.claude import Reporter, _truncate, running_subagents


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


def test_running_subagents_lists_the_background_subagents():
    assert running_subagents(_tasks_changed([_subagent("investigator")])) == [
        "investigator"
    ]


def test_running_subagents_is_empty_once_they_finish():
    assert running_subagents(_tasks_changed([])) == []


def test_background_shell_tasks_are_not_subagents():
    shell = {"task_id": "b1", "task_type": "local_bash"}
    assert running_subagents(_tasks_changed([shell])) == []


def test_other_messages_report_nothing():
    assert running_subagents(SystemMessage(subtype="init", data={})) is None
