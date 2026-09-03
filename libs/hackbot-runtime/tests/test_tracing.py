"""Tests for Weave tracing setup and agent-name derivation."""

import pytest
from hackbot_runtime import tracing, wandb_wif
from weave.conversation.agent_context import resolve_agent_name as weave_agent_name


def _entrypoint_at(path: str):
    """A function whose source file is ``path`` (mimics an agent's main())."""
    ns: dict = {}
    exec(compile("def main(): pass", path, "exec"), ns)
    return ns["main"]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for var in ("WANDB_API_KEY", wandb_wif.TOKEN_FILE_ENV, "WEAVE_PROJECT"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/app/hackbot_agents/build_repair/__main__.py", "build-repair"),
        ("/x/hackbot_agents/test_plan_generator/__main__.py", "test-plan-generator"),
        ("/venv/site-packages/hackbot_agents/bug_fix/__main__.py", "bug-fix"),
    ],
)
def test_resolve_agent_name_from_source_path(path, expected):
    assert tracing.resolve_agent_name(_entrypoint_at(path)) == expected


def test_init_weave_is_inert_without_credentials(monkeypatch):
    import weave

    monkeypatch.setattr(
        weave, "init", lambda *a, **k: pytest.fail("weave.init should not be called")
    )
    assert tracing._init_weave() is False


def test_init_weave_enabled_by_api_key(monkeypatch):
    import weave

    calls = {}
    monkeypatch.setattr(
        weave, "init", lambda project, **kw: calls.update(project=project)
    )
    monkeypatch.setenv("WANDB_API_KEY", "dummy")
    monkeypatch.setenv("WEAVE_PROJECT", "team/prod")

    assert tracing._init_weave() is True
    assert calls == {"project": "team/prod"}


def test_init_weave_enabled_by_wif_token_file(monkeypatch):
    """Federation leaves no API key -- the identity token file enables tracing."""
    import weave

    calls = {}
    monkeypatch.setattr(
        weave, "init", lambda project, **kw: calls.update(project=project)
    )
    monkeypatch.setenv(wandb_wif.TOKEN_FILE_ENV, "/run/wandb-identity-token")

    assert tracing._init_weave() is True
    assert calls == {"project": tracing.DEFAULT_WEAVE_PROJECT}


def test_trace_agent_is_noop_without_credentials():
    entry = _entrypoint_at("/app/hackbot_agents/build_repair/__main__.py")
    with tracing.trace_agent(entry):
        assert weave_agent_name("claude_agent_sdk") == "claude_agent_sdk"


def test_trace_agent_labels_spans_when_enabled(monkeypatch):
    monkeypatch.setattr(tracing, "_init_weave", lambda: True)
    entry = _entrypoint_at("/app/hackbot_agents/build_repair/__main__.py")

    with tracing.trace_agent(entry):
        assert weave_agent_name("claude_agent_sdk") == "build-repair"
    assert weave_agent_name("claude_agent_sdk") == "claude_agent_sdk"
