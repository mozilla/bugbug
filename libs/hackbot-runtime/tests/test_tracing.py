"""Tests for Weave tracing setup and agent-name derivation."""

import pytest
from hackbot_runtime import tracing, wif
from opentelemetry.sdk.trace import TracerProvider
from weave.conversation.agent_context import resolve_agent_name as weave_agent_name


def _entrypoint_at(path: str):
    """A function whose source file is ``path`` (mimics an agent's main())."""
    ns: dict = {}
    exec(compile("def main(): pass", path, "exec"), ns)
    return ns["main"]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for var in ("WANDB_API_KEY", wif.WandbWifSettings.token_file_env, "WEAVE_PROJECT"):
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
    monkeypatch.setenv(wif.WandbWifSettings.token_file_env, "/run/wandb-identity-token")

    assert tracing._init_weave() is True
    assert calls == {"project": tracing.DEFAULT_WEAVE_PROJECT}


def test_trace_agent_is_noop_without_credentials():
    entry = _entrypoint_at("/app/hackbot_agents/build_repair/__main__.py")
    with tracing.trace_agent(entry, "run-1"):
        assert weave_agent_name("claude_agent_sdk") == "claude_agent_sdk"


def test_trace_agent_labels_spans_when_enabled(monkeypatch):
    monkeypatch.setattr(tracing, "_init_weave", lambda: True)
    entry = _entrypoint_at("/app/hackbot_agents/build_repair/__main__.py")

    with tracing.trace_agent(entry, "run-1"):
        assert weave_agent_name("claude_agent_sdk") == "build-repair"
    assert weave_agent_name("claude_agent_sdk") == "claude_agent_sdk"


def test_trace_agent_tags_spans_with_run_id(monkeypatch):
    monkeypatch.setattr(tracing, "_init_weave", lambda: True)
    provider = TracerProvider()
    monkeypatch.setattr(tracing.otel_trace, "get_tracer_provider", lambda: provider)
    entry = _entrypoint_at("/app/hackbot_agents/build_repair/__main__.py")

    with (
        tracing.trace_agent(entry, "run-1"),
        provider.get_tracer("test").start_as_current_span("turn") as span,
    ):
        pass

    assert span.attributes[tracing.RUN_ID_SPAN_ATTRIBUTE] == "run-1"


def test_trace_agent_skips_tagging_without_sdk_provider(monkeypatch):
    monkeypatch.setattr(tracing, "_init_weave", lambda: True)
    monkeypatch.setattr(tracing.otel_trace, "get_tracer_provider", lambda: object())
    entry = _entrypoint_at("/app/hackbot_agents/build_repair/__main__.py")

    with tracing.trace_agent(entry, "run-1"):
        pass


@pytest.fixture
def exporter(monkeypatch):
    """Capture Weave conversation spans of a traced bug-fix run in memory."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from weave.conversation import conversation

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(conversation.otel_trace, "get_tracer", provider.get_tracer)
    monkeypatch.setattr(tracing.otel_trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(tracing, "_init_weave", lambda: True)
    return exporter


BUG_FIX = _entrypoint_at("/app/hackbot_agents/bug_fix/__main__.py")


def test_trace_agent_labels_subagents_as_parent_agent(exporter):
    from weave.conversation import SubAgent

    start = SubAgent.start

    with tracing.trace_agent(BUG_FIX, "run-1"):
        SubAgent(name="investigator").start(set_current=False).end()

    (span,) = exporter.get_finished_spans()
    assert span.attributes["gen_ai.agent.name"] == "bug-fix"
    assert span.attributes[tracing.SUBAGENT_SPAN_ATTRIBUTE] == "investigator"
    assert SubAgent.start is start


def _usage(input, cache_read, cache_creation, output):
    return {
        "input_tokens": input,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
        "output_tokens": output,
    }


def _model_usage(input, cache_read, cache_creation, output):
    return {
        "inputTokens": input,
        "cacheReadInputTokens": cache_read,
        "cacheCreationInputTokens": cache_creation,
        "outputTokens": output,
    }


def test_trace_agent_records_usage_missing_from_main_thread(exporter):
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )
    from weave.conversation import Conversation
    from weave.integrations.claude_agent_sdk import otel_integration

    def result(usage, model_usage):
        return ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s1",
            usage=usage,
            model_usage=model_usage,
        )

    first_turn = [
        AssistantMessage(
            content=[
                ToolUseBlock(
                    id="toolu_1", name="Agent", input={"subagent_type": "investigator"}
                )
            ],
            model="claude-opus-5-5",
        ),
        AssistantMessage(
            content=[TextBlock(text="found it")],
            model="claude-opus-5-5",
            parent_tool_use_id="toolu_1",
        ),
        SystemMessage(
            subtype="task_notification",
            data={"task_id": "a1", "tool_use_id": "toolu_1", "status": "completed"},
        ),
        UserMessage(
            content=[ToolResultBlock(tool_use_id="toolu_1", content="found it")]
        ),
        AssistantMessage(content=[TextBlock(text="done")], model="claude-opus-5-5"),
        result(
            _usage(4, 18081, 18562, 174),
            {
                "claude-opus-5-5": _model_usage(12, 28188, 22135, 436),
                "claude-haiku-5-5": _model_usage(1235, 0, 0, 115),
            },
        ),
    ]
    second_turn = [
        AssistantMessage(content=[TextBlock(text="again")], model="claude-opus-5-5"),
        result(
            _usage(1, 36000, 100, 10),
            {
                "claude-opus-5-5": _model_usage(13, 64188, 22235, 446),
                "claude-haiku-5-5": _model_usage(1235, 0, 0, 115),
            },
        ),
    ]

    with tracing.trace_agent(BUG_FIX, "run-1"), Conversation() as conv:
        for messages in (first_turn, second_turn):
            with conv.start_turn(agent_name="bug-fix") as turn:
                state = otel_integration._TurnState(conversation=conv, turn=turn)
                for msg in messages:
                    otel_integration._process_message_for_turn(msg, state)
                otel_integration._finalize_turn(state)

    spans = exporter.get_finished_spans()
    haiku = [
        s
        for s in spans
        if s.attributes.get("gen_ai.request.model") == "claude-haiku-5-5"
    ]
    assert len(haiku) == 1
    unattributed = {
        s.attributes["gen_ai.request.model"]: s.attributes
        for s in spans
        if s.attributes.get(tracing.SUBAGENT_SPAN_ATTRIBUTE) == "investigator"
        and "gen_ai.usage.output_tokens" in s.attributes
    }
    assert unattributed.keys() == {"claude-opus-5-5", "claude-haiku-5-5"}
    opus = unattributed["claude-opus-5-5"]
    assert opus["gen_ai.usage.input_tokens"] == 8 + 10107 + 3573
    assert opus["gen_ai.usage.output_tokens"] == 262
    assert unattributed["claude-haiku-5-5"]["gen_ai.usage.output_tokens"] == 115


def test_trace_agent_tags_spans_inside_subagent(exporter):
    from weave.conversation import Conversation

    with (
        tracing.trace_agent(BUG_FIX, "run-1"),
        Conversation() as conv,
        conv.start_turn(agent_name="bug-fix") as turn,
    ):
        subagent = turn.start_subagent(name="duplicate_hunter").start(set_current=False)
        with subagent.start_llm(model="claude-opus-5-5"):
            pass
        with subagent.start_tool(name="Bash"):
            pass
        subagent.end()

    tags = sorted(
        (s.name, s.attributes.get(tracing.SUBAGENT_SPAN_ATTRIBUTE, ""))
        for s in exporter.get_finished_spans()
    )
    assert tags == [
        ("chat claude-opus-5-5", "duplicate_hunter"),
        ("execute_tool Bash", "duplicate_hunter"),
        ("invoke_agent bug-fix", ""),
        ("invoke_agent bug-fix", "duplicate_hunter"),
    ]
