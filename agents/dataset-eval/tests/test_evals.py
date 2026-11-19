import asyncio
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from hackbot_agents.dataset_eval import dataset, prices, runner, tree
from hackbot_agents.dataset_eval.grading import checks, tests
from hackbot_agents.dataset_eval.isolation import example_env, run_isolated
from hackbot_agents.dataset_eval.prompt_executor.harnesses import harness_env
from hackbot_agents.dataset_eval.prompt_executor.harnesses.claude_code import _usage
from hackbot_agents.dataset_eval.prompt_executor.harnesses.codex import (
    _record_usage,
    normalize,
    to_messages,
    trace,
)
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
    usage = {
        "total": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 7}
    }
    run = normalize(items, usage, "gpt-x")
    roles = [t["role"] for t in run.transcript]
    assert roles == ["assistant", "tool_call", "tool_result", "assistant"]
    assert run.transcript[0]["thinking"] == "look" and run.tool_errors == 1
    assert run.usage == {
        "gpt-x": {"input_tokens": 20, "cache_read_input_tokens": 80, "output_tokens": 7}
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
            trace(turn, to_messages(item, "gpt-x"))
        usage = {"input_tokens": 20, "cache_read_input_tokens": 80}
        _record_usage(turn, usage, "gpt-x")

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "execute_tool shell" in spans and "chat gpt-x" in spans
    assert spans["execute_tool shell"].status.status_code.name == "ERROR"
    assert any(n.startswith("invoke_agent") for n in spans)
