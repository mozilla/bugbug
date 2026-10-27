from __future__ import annotations

import pytest
from performance_tools.inputs import BugIdInput
from performance_tools.tasks import RunTracker, TaskConfig


class _FakeResultMsg:
    """Stands in for claude_agent_sdk's ResultMessage.

    `end_task` reads num_turns and total_cost_usd off it; verify the real
    attribute names against the SDK before relying on this.
    """

    def __init__(self, num_turns: int, total_cost_usd: float) -> None:
        self.num_turns = num_turns
        self.total_cost_usd = total_cost_usd


def test_run_tracker_keys_start_times_by_task_name():
    """Concurrent tasks must not clobber each other's start time.

    A single-slot version silently dropped one task's turns and cost, understating
    the cost of every run that measured two things at once.
    """
    t = RunTracker()
    t.start_task("a")
    t.start_task("b")
    t.end_task("a", _FakeResultMsg(3, 1.0))
    t.end_task("b", _FakeResultMsg(5, 2.0))
    assert t.num_turns == 8
    assert t.total_cost_usd == pytest.approx(3.0)


def test_end_without_start_still_records_cost():
    """A missing start time costs a duration, not the turns and cost."""
    t = RunTracker()
    t.end_task("never-started", _FakeResultMsg(2, 0.5))
    assert t.num_turns == 2
    assert t.total_cost_usd == pytest.approx(0.5)


def test_bug_id_input_reports_its_subject():
    assert BugIdInput(bug_id=123).subject() == "bug 123"


def test_task_config_defaults_are_none():
    config = TaskConfig()
    assert config.model is None
    assert config.max_turns is None
    assert config.effort is None
    assert config.log is None
    assert config.verbose is True


def test_system_prompt_template_ships_with_the_lib():
    """`Task.system_prompt()` reads `prompts/system.md` beside itself.

    The failure of a missing template is at RUN time, not import time, so it is easy
    to miss: a Task subclass with no template raises FileNotFoundError on its first
    turn, long after every construction-only test has passed. This pins it in place.
    """
    from performance_tools import tasks

    template = tasks.HERE / "prompts" / "system.md"
    assert template.exists()
    assert template.read_text().strip()
    # The template is a format string; a concrete Task fills this in.
    assert "{task_details}" in template.read_text()
