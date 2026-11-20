import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from hackbot_agents.dataset_eval import dataset, prices, runner, tree
from hackbot_agents.dataset_eval.grading import checks, tests
from hackbot_agents.dataset_eval.isolation import example_env, run_isolated
from hackbot_agents.dataset_eval.prompt_executor.harnesses import (
    Run,
    harness_env,
    otari,
    record,
    record_usage,
    trace,
)
from hackbot_agents.dataset_eval.prompt_executor.harnesses.claude_code import _usage
from hackbot_agents.dataset_eval.prompt_executor.harnesses.codex import (
    to_messages,
    usage_of,
)
from hackbot_agents.dataset_eval.prompt_executor.harnesses.oh_my_pi import OhMyPi
from hackbot_agents.dataset_eval.weave_log import WeaveLog


def _entry(status, **subtests):
    return {"status": status, "subtests": subtests}


def test_score():
    baseline = {
        "a.html": _entry("OK", one="FAIL", two="FAIL"),
        "b.html": _entry("ERROR"),
        "c.html": _entry("OK", one="PASS", two="FAIL"),
        "ref.html": _entry("PASS"),
    }
    after = {
        "a.html": _entry("OK", one="PASS", two="FAIL"),
        "b.html": _entry("OK", x="PASS", y="PASS", z="PASS", w="FAIL"),
        "c.html": _entry("OK", one="FAIL", two="PASS"),
        "ref.html": _entry("FAIL"),
    }
    scores = tests.score(baseline, after)
    # a: 0 -> .5, b: 0 -> .75, c: .5 -> .5; ref was passing.
    assert scores["test_progress"] == pytest.approx((0.5 + 0.75 + 0.0) / 3)
    assert scores["test_regressions"] == 1.0


def test_codex_items_and_usage():
    items = [
        {"type": "userMessage", "id": "0"},
        {"type": "reasoning", "id": "1", "summary": ["look"]},
        {
            "type": "commandExecution",
            "id": "2",
            "command": "ls",
            "aggregated_output": "x",
            "exit_code": 1,
        },
        {"type": "agentMessage", "id": "3", "text": "done"},
    ]
    run = Run()
    for item in items:
        for msg in to_messages(item, "gpt-x"):
            record(run, msg)
    roles = [t["role"] for t in run.transcript]
    assert roles == ["assistant", "tool_call", "tool_result", "assistant"]
    assert run.transcript[0]["thinking"] == "look" and run.tool_errors == 1
    usage = {
        "total": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 7}
    }
    assert usage_of(usage) == {
        "input_tokens": 20,
        "cache_read_input_tokens": 80,
        "output_tokens": 7,
    }


def test_claude_usage_by_model():
    result = SimpleNamespace(
        model_usage={
            "claude-opus-5-5": {"inputTokens": 3, "outputTokens": 4, "costUSD": 1.0}
        },
        usage=None,
    )
    assert _usage(result, []) == {
        "claude-opus-5-5": {"input_tokens": 3, "output_tokens": 4}
    }


def test_harness_env_keeps_only_own_vendor(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_FEDERATION_RULE_ID", "a")
    monkeypatch.setenv("CODEX_API_KEY", "c")
    claude, codex = harness_env("anthropic"), harness_env("openai")
    assert "ANTHROPIC_FEDERATION_RULE_ID" in claude and "CODEX_API_KEY" not in claude
    assert "CODEX_API_KEY" in codex and "ANTHROPIC_FEDERATION_RULE_ID" not in codex


def test_cost():
    usage = {
        "claude-opus-5-5": {
            "input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
        }
    }
    assert prices.cost(usage) == pytest.approx(4.0 + 0.4)
    assert prices.cost({"unknown": {"input_tokens": 1}}) is None
    codex = {
        "gpt-6.1-sol": {
            "input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        }
    }
    assert prices.cost(codex) == pytest.approx(2.0 + 0.10 + 10.0)


PATCH = (
    "diff --git a/dom/base/Feature.cpp b/dom/base/Feature.cpp\n+code\n-old\n"
    "diff --git a/testing/web-platform/meta/x.ini b/testing/web-platform/meta/x.ini\n+x\n"
)


def test_patch_facts():
    assert checks.touches(PATCH, "testing/web-platform")
    assert not checks.touches(PATCH, "testing/web")
    assert checks.lines_changed(PATCH) == 3


def test_local_dataset(tmp_path):
    (tmp_path / "feature.json").write_text(
        json.dumps({"prompt": "Do it.", "tests": []})
    )
    assert dataset.load(str(tmp_path)) == [
        {"id": "feature", "prompt": "Do it.", "tests": []}
    ]


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_tree_reset_keeps_only_the_build(tmp_path):
    repo = tmp_path / "firefox"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("/.mozconfig*\n/obj*/\n")
    (repo / "tests").mkdir()
    (repo / "tests/a.ini").write_text("expected: FAIL\n")
    (repo / "dom.cpp").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = tree.head(repo)
    (repo / ".mozconfig").write_text("left by an older evaluator\n")
    assert tree.mozconfig(repo).read_text() == tree.MOZCONFIG
    (repo / tree.OBJDIR).mkdir()
    (repo / tree.OBJDIR / "libxul.so").write_text("built")

    (repo / "dom.cpp").write_text("impl\n")
    (repo / "new.cpp").write_text("new\n")
    (repo / "tests/a.ini").write_text("expected: PASS\n")
    _git(repo, "commit", "-qam", "agent commit")
    patch = tree.diff(repo, base)
    assert "b/new.cpp" in patch and "b/dom.cpp" in patch and "libxul" not in patch

    tree.restore(repo, base, "tests")
    assert (repo / "tests/a.ini").read_text() == "expected: FAIL\n"
    assert (repo / "dom.cpp").read_text() == "impl\n"

    tree.reset(repo, base)
    assert (repo / "dom.cpp").read_text() == "base\n" and not (
        repo / "new.cpp"
    ).exists()
    assert (repo / ".mozconfig").exists()
    assert (repo / tree.OBJDIR / "libxul.so").read_text() == "built"


def test_example_env_is_fresh_and_shares_the_toolchain(tmp_path):
    env = example_env({"HOME": "/home/agent"}, tmp_path / "ex", {"PROMPT": "p"})
    assert env["HOME"] == str(tmp_path / "ex" / "home")
    assert env["CLAUDE_CONFIG_DIR"].startswith(env["HOME"])
    assert env["CODEX_HOME"].startswith(env["HOME"])
    assert Path(env["CODEX_HOME"]).is_dir() and Path(env["CLAUDE_CONFIG_DIR"]).is_dir()
    assert env["MOZBUILD_STATE_PATH"] == "/home/agent/.mozbuild"
    assert env["PROMPT"] == "p" and Path(env["TMPDIR"]).is_dir()


def test_run_isolated_times_out_and_kills_the_group(tmp_path):
    marker = tmp_path / "child"
    script = f"sleep 30 & echo $! > {marker}; wait"
    started = time.monotonic()
    code = asyncio.run(
        run_isolated(
            ["sh", "-c", script],
            {"PATH": "/bin:/usr/bin"},
            tmp_path,
            1,
            tmp_path / "log",
        )
    )
    assert code is None and time.monotonic() - started < 10
    time.sleep(0.2)
    pid = marker.read_text().strip()
    assert subprocess.run(["kill", "-0", pid], capture_output=True).returncode != 0


def test_weave_log_is_a_no_op_without_a_client():
    log = WeaveLog("exp", {"model": "m"}, "ds:v1", [{"id": "a", "prompt": "p"}])
    log.finish(log.start({"id": "a"}, "a", 0), {"patch": ""}, {"test_progress": 0.5})
    log.summary({})
    assert log.url is None


def test_label_is_appended_to_the_experiment_name():
    exp = runner.Experiment(
        "ds", "prompt-executor", "claude_code", "m", None, 1, 30, "j", None
    )
    assert exp.name == "prompt-executor/claude_code/m/default"
    exp.label = "full-task-prompt"
    assert exp.name == "prompt-executor/claude_code/m/default:full-task-prompt"


def test_summarize():
    records = [
        {"scores": {"test_progress": 0.5, "builds": 1.0}, "cost_usd": 1.0},
        {"scores": {"test_progress": 1.0, "builds": 1.0}, "cost_usd": None},
    ]
    summary = runner.summarize(records, errors=1)
    assert summary["examples"] == 2 and summary["errors"] == 1
    assert summary["test_progress"] == 0.75 and summary["cost_usd_total"] == 1.0


def test_grade(monkeypatch, tmp_path):
    restored, ran, built = [], [], [True]

    async def build(*args):
        return {"success": built[0], "stdout": "error: oops"}

    async def run_tests(src, harness, paths, report, prefs):
        ran.append((harness, paths, prefs))
        return {"a.html": _entry("OK", one="PASS")}

    monkeypatch.setattr(runner, "build_firefox", build)
    monkeypatch.setattr(runner, "run_tests", run_tests)
    monkeypatch.setattr(
        runner.tree, "restore", lambda src, base, path: restored.append(path)
    )
    grader = object.__new__(runner.Runner)
    grader.work = tmp_path
    suite = {"harness": "wpt", "paths": ["dom/x"], "prefs": {"dom.x.enabled": True}}
    baseline = {"0:a.html": _entry("OK", one="FAIL", two="PASS")}

    def grade(row, patch=PATCH, base=None):
        row = {"id": "x", **row}
        return asyncio.run(grader.grade(tmp_path, "base", row, 0, base, patch))

    assert grade({}) == {"builds": 1.0}
    # Editing test metadata is fine; it is restored like the tests.
    scores = grade({"tests": [suite]}, base=baseline)
    assert scores["integrity"] == 1.0 and scores["test_progress"] == 1.0
    assert restored == ["testing/web-platform"]
    assert ran == [("wpt", ["dom/x"], {"dom.x.enabled": True})]
    edits_tests = "diff --git a/testing/web-platform/tests/dom/x/a.html b/x\n+x\n"
    assert grade({"tests": [suite]}, edits_tests, baseline)["integrity"] == 0.0
    # A failed build scores no progress and no made-up regressions.
    built[0] = False
    assert grade({"tests": [suite]}, base=baseline) == {
        "integrity": 1.0,
        "builds": 0.0,
        "test_progress": 0.0,
    }


def test_timed_out_attempt_keeps_its_patch(monkeypatch, tmp_path):
    async def timed_out(*args):
        return None

    monkeypatch.setattr(runner, "run_isolated", timed_out)
    monkeypatch.setattr(runner.tree, "diff", lambda src, base: PATCH)
    attempt = object.__new__(runner.Runner)
    attempt.work, attempt.ctx = tmp_path, SimpleNamespace(run_id="r")
    attempt.exp = runner.Experiment(
        "ds", "prompt-executor", "claude_code", "m", None, 1, 30, "j", None
    )
    out, patch = asyncio.run(
        attempt.attempt(tmp_path, "base", {"id": "x", "prompt": "p"}, 0)
    )
    assert out == {"timed_out": True} and patch == PATCH


def test_run_isolated_tees_output(tmp_path, capsys):
    code = asyncio.run(
        run_isolated(
            ["sh", "-c", "echo step one"],
            {"PATH": "/bin:/usr/bin"},
            tmp_path,
            10,
            tmp_path / "log",
            "[x] ",
        )
    )
    assert code == 0
    assert (tmp_path / "log").read_text() == "step one\n"
    assert "[x] step one" in capsys.readouterr().out


def test_codex_items_are_reported_like_claude_messages(capsys):
    from hackbot_runtime.claude import Reporter

    item = {"type": "commandExecution", "id": "1", "command": "ls", "exit_code": 2}
    with Reporter(verbose=True, log_path=None) as reporter:
        for msg in to_messages(item, "gpt-x"):
            reporter.message(msg)
    out = capsys.readouterr().out
    assert "shell" in out and "ls" in out and "ERROR" in out


def test_codex_items_are_traced_as_a_weave_conversation():
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from weave.conversation import start_conversation

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)

    items = [
        {"type": "reasoning", "id": "1", "summary": ["look"]},
        {"type": "commandExecution", "id": "2", "command": "ls", "exit_code": 1},
        {"type": "agentMessage", "id": "3", "text": "done"},
    ]
    conversation = start_conversation(agent_name="prompt-executor", model="gpt-x")
    with conversation, conversation.start_turn(user_message="Do it.") as turn:
        for item in items:
            trace(turn, to_messages(item, "gpt-x"), "openai")
        usage = {"input_tokens": 20, "cache_read_input_tokens": 80}
        record_usage(turn, usage, "gpt-x", "openai")

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "execute_tool shell" in spans and "chat gpt-x" in spans
    assert spans["execute_tool shell"].status.status_code.name == "ERROR"
    assert any(n.startswith("invoke_agent") for n in spans)


def _fake_cli(
    tmp_path, monkeypatch, name: str, events: list, then: str = "", stored=({}, None)
) -> None:
    """Put ``name`` on PATH: it saves its stdin, env and args and prints ``events``.

    otari is stubbed: it serves any model and reports ``stored`` as the run's usage.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / name
    cli.write_text(
        "#!/bin/sh\n"
        f'cat > {tmp_path}/stdin; env > {tmp_path}/env; echo "$@" > {tmp_path}/args\n'
        + "".join(
            f"echo '{e if isinstance(e, str) else json.dumps(e)}'\n" for e in events
        )
        + then
    )
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("OTARI_API_KEY", "k")
    monkeypatch.setenv("OTARI_BASE_URL", "https://otari.test/api")
    monkeypatch.setenv("HOME", str(tmp_path))

    async def usage(tag, model):
        return stored

    async def served(m):
        return otari.Model(m, 1000, 100, True)

    monkeypatch.setattr(otari, "model", served)
    monkeypatch.setattr(otari, "usage", usage)


def _env(tmp_path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (tmp_path / "env").read_text().splitlines()
        if "=" in line
    )


def test_oh_my_pi_events(tmp_path, monkeypatch):
    usage = {"input": 10, "output": 5, "cacheRead": 90, "cacheWrite": 0}
    call = {
        "role": "assistant",
        "provider": "otari",
        "model": "deepinfra:qwen",
        "content": [
            {"type": "thinking", "thinking": "look"},
            {"type": "toolCall", "id": "t1", "name": "bash", "arguments": {"c": "ls"}},
        ],
        "usage": usage,
        "stopReason": "toolUse",
    }
    result = {
        "role": "toolResult",
        "toolCallId": "t1",
        "content": [{"type": "text", "text": "x"}],
        "isError": True,
    }
    answer = {
        **call,
        "content": [{"type": "text", "text": "done"}],
        "stopReason": "stop",
    }
    events = [
        "Warning: not JSON",
        {"type": "message_end", "message": {"role": "user", "content": "Do it."}},
        {"type": "message_end", "message": call},
        {"type": "message_end", "message": result},
        {"type": "turn_end", "message": call, "toolResults": [result]},
        {"type": "message_end", "message": answer},
        {"type": "turn_end", "message": answer, "toolResults": []},
    ]
    # With a subagent's 100 input tokens that the stream did not show.
    stored = {"deepinfra:qwen": {"input_tokens": 120, "cache_read_input_tokens": 180}}
    _fake_cli(tmp_path, monkeypatch, "omp", events, stored=(stored, 0.75))

    run = Run(transcript=[{"role": "user", "content": "Do it."}])
    asyncio.run(OhMyPi().run("Do it.", tmp_path, "deepinfra:qwen", "high", run))

    assert run.message == "done" and run.error is None
    roles = [t["role"] for t in run.transcript]
    assert roles == ["user", "tool_call", "tool_result", "assistant"]
    assert run.tool_errors == 1 and run.models == ["deepinfra:qwen"]
    assert run.usage["deepinfra:qwen"]["input_tokens"] == 120
    assert run.cost_usd == 0.75
    assert (tmp_path / "stdin").read_text() == "Do it."
    args = (tmp_path / "args").read_text()
    assert "--model otari/deepinfra:qwen" in args and "--thinking high" in args
    config = json.loads((tmp_path / ".omp" / "agent" / "models.yml").read_text())
    provider = config["providers"]["otari"]
    assert provider["baseUrl"] == "https://otari.test/api/v1"
    assert provider["headers"]["Otari-Tags"].startswith("hackbot_attempt=")
    assert provider["models"][0]["contextWindow"] == 1000
    env = _env(tmp_path)
    assert "ANTHROPIC_API_KEY" not in env and env["OTARI_API_KEY"] == "k"
    assert env["PWD"] == str(tmp_path)


def test_a_response_is_one_chat_span_with_its_usage():
    from claude_agent_sdk import AssistantMessage, TextBlock, ThinkingBlock
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from weave.conversation import start_conversation

    if not isinstance(otel_trace.get_tracer_provider(), TracerProvider):
        otel_trace.set_tracer_provider(TracerProvider())
    exporter = InMemorySpanExporter()
    otel_trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    messages = [
        AssistantMessage([ThinkingBlock("look", "")], "m"),
        AssistantMessage([TextBlock("done")], "m"),
    ]
    usage = {"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 90}
    conversation = start_conversation(agent_name="prompt-executor", model="m")
    with conversation, conversation.start_turn(user_message="Do it.") as turn:
        trace(turn, messages, "otari", usage)

    (chat,) = [s for s in exporter.get_finished_spans() if s.name == "chat m"]
    assert chat.attributes["gen_ai.usage.input_tokens"] == 100
    assert chat.attributes["gen_ai.usage.cache_read.input_tokens"] == 90


def test_otari_prices_are_registered_once_and_again_when_they_change(monkeypatch):
    import weave

    added = []

    class Client:
        def query_costs(self, llm_ids):
            return [SimpleNamespace(**cost) for cost in added]

        def add_cost(self, llm_id, provider_id, effective_date, **costs):
            # Weave stores the rates as 32-bit floats.
            rounded = {k: float(f"{v:.8g}") for k, v in costs.items()}
            added.append({"effective_date": effective_date, **rounded})

    monkeypatch.setattr(weave, "get_client", lambda: Client())
    pricing = {
        "input_price_per_million": 0.15,
        "output_price_per_million": 0.6,
        "cache_read_price_per_million": 0.003,
        "unit": "token",
    }
    model = otari.Model("m", None, None, False, pricing)

    asyncio.run(otari.register_price(model))
    asyncio.run(otari.register_price(model))
    assert len(added) == 1 and added[0]["effective_date"].year == 2000
    model.pricing = {**pricing, "output_price_per_million": 0.9}
    asyncio.run(otari.register_price(model))
    assert len(added) == 2 and added[1]["effective_date"].year > 2000


def test_otari_usage_of():
    totals = {
        "request_count": 3,
        "billed_input_tokens": 100,
        "billed_output_tokens": 7,
        "cache_read_tokens": 80,
        "cache_write_tokens": 5,
        "cost": 0.25,
    }
    assert otari.usage_of({"totals": totals}, "m") == (
        {
            "m": {
                "input_tokens": 15,
                "output_tokens": 7,
                "cache_read_input_tokens": 80,
                "cache_creation_input_tokens": 5,
            }
        },
        0.25,
    )
    unpriced = {**totals, "unpriced_requests": 1}
    assert otari.usage_of({"totals": unpriced}, "m")[1] is None
    assert otari.usage_of({"totals": {"request_count": 0}}, "m") == ({}, None)


def test_otari_settle_adds_what_the_stream_left_out(monkeypatch):
    from weave.conversation import start_conversation

    stored = {"m": {"input_tokens": 15, "output_tokens": 4}}

    async def usage(tag, model):
        return stored, 2.0

    monkeypatch.setattr(otari, "usage", usage)
    run = Run(usage={"m": {"input_tokens": 10, "output_tokens": 9}})
    model = otari.Model("m", None, None, False)
    conversation = start_conversation(agent_name="prompt-executor", model="m")
    with conversation, conversation.start_turn(user_message="Do it.") as turn:
        asyncio.run(otari.settle(run, "t", model, turn))

    assert run.usage == {"m": {"input_tokens": 15, "output_tokens": 9}}
    assert run.cost_usd == 2.0


def test_otari_settle_prices_the_stream_without_usage_access(monkeypatch):
    import httpx

    async def forbidden(tag, model):
        raise httpx.HTTPStatusError(
            "403", request=httpx.Request("GET", "x"), response=httpx.Response(403)
        )

    monkeypatch.setattr(otari, "usage", forbidden)
    pricing = {
        "input_price_per_million": 1.0,
        "output_price_per_million": 2.0,
        "cache_read_price_per_million": 0.1,
        "unit": "token",
    }
    model = otari.Model("m", None, None, False, pricing)
    run = Run(
        usage={
            "m": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "cache_read_input_tokens": 1_000_000,
            }
        }
    )
    asyncio.run(otari.settle(run, "t", model, None))

    assert run.cost_usd == pytest.approx(1.0 + 2.0 + 0.1)

    # otari has written only part of the run yet: its cost is too low.
    async def partial(tag, name):
        return {}, 0.5

    monkeypatch.setattr(otari, "usage", partial)
    asyncio.run(otari.settle(run, "t", model, None))
    assert run.cost_usd == pytest.approx(1.0 + 2.0 + 0.1)

    async def not_json(tag, name):
        raise ValueError("not JSON")

    monkeypatch.setattr(otari, "usage", not_json)
    asyncio.run(otari.settle(run, "t", model, None))
    assert run.cost_usd == pytest.approx(1.0 + 2.0 + 0.1)
