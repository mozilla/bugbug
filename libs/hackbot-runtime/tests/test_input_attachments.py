"""Tests for how a run's attachments are described to the agent."""

from pathlib import Path

from hackbot_runtime import input_attachments_prompt


def test_a_run_without_attachments_adds_nothing():
    assert input_attachments_prompt([]) == ""


def test_attachments_are_listed_by_path():
    prompt = input_attachments_prompt([Path("/tmp/in/a.png"), Path("/tmp/in/b.log")])

    assert "- /tmp/in/a.png\n- /tmp/in/b.log\n" in prompt
    # Agents put it straight before the rest of their prompt.
    assert prompt.endswith("\n\n")
