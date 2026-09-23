"""Tests for ``build_options`` and ``check_result``.

Both are pure: the options object is asserted on without running anything, and
the result guard is fed lightweight stand-in result objects.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from hackbot_agents.uplift_merge_conflict_resolver.agent import (
    build_options,
    check_result,
)
from hackbot_agents.uplift_merge_conflict_resolver.models import MODEL

MCP_SERVERS = {"bugbug": {"type": "http", "url": "http://localhost:8080/mcp"}}


def make_options(**overrides):
    kwargs = dict(
        source_repo=Path("/repo"),
        scratch_out=Path("/scratch"),
        mcp_servers=MCP_SERVERS,
    )
    kwargs.update(overrides)
    return build_options(**kwargs)


def test_options_default_model():
    options = make_options()
    assert options.model == MODEL, "Without an override, the configured model is used."


def test_options_leave_effort_to_the_api_default():
    options = make_options()
    assert options.effort is None, (
        "Without an override, `effort` is omitted so the API's own default applies."
    )


def test_options_accept_overrides():
    options = make_options(model="custom-model", effort="low", max_turns=12)
    assert options.model == "custom-model", (
        "An explicit model should override the default."
    )
    assert options.effort == "low", "An explicit effort should override the default."
    assert options.max_turns == 12, "`max_turns` should be passed through."


def test_options_use_the_claude_code_system_prompt():
    options = make_options()
    assert options.system_prompt == {"type": "preset", "preset": "claude_code"}, (
        "The agent is a Claude Code session, so it should get Claude Code's own "
        "system prompt rather than the SDK's minimal default; what this agent "
        "has to say is task-specific and travels in the user prompt."
    )


def test_options_load_project_settings_like_a_local_session():
    options = make_options()
    assert options.setting_sources == ["project"], (
        "`project` loads `CLAUDE.md` and the in-tree skills; `local` would only "
        "name a gitignored file a fresh clone cannot have."
    )


def test_options_run_unattended_with_every_tool_but_questions():
    options = make_options()
    assert options.permission_mode == "bypassPermissions", (
        "Nobody is present to approve a tool call; the container is the sandbox."
    )
    assert "AskUserQuestion" in options.disallowed_tools, (
        "Interactive questions should be disabled in an unattended run."
    )
    assert "Task" not in options.disallowed_tools, (
        "Sub-agents stay enabled, like a local Claude Code session."
    )


def test_check_result_raises_when_no_result():
    with pytest.raises(Exception) as excinfo:
        check_result(None, "beta")
    assert "no result" in str(excinfo.value), "A missing result should explain itself."


def test_check_result_raises_on_error_with_message():
    failed = SimpleNamespace(is_error=True, result="boom", subtype="error_max_turns")
    with pytest.raises(Exception) as excinfo:
        check_result(failed, "beta")
    assert "boom" in str(excinfo.value), (
        "The failure message should surface in the error."
    )


def test_check_result_falls_back_to_subtype():
    failed = SimpleNamespace(is_error=True, result=None, subtype="error_max_turns")
    with pytest.raises(Exception) as excinfo:
        check_result(failed, "beta")
    assert "error_max_turns" in str(excinfo.value), (
        "With no message, the subtype should be reported instead."
    )


def test_check_result_passes_on_success():
    ok = SimpleNamespace(is_error=False, result="done", subtype="success")
    check_result(ok, "beta")  # Should not raise.
