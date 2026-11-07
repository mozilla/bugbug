# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Run one experiment: every example in turn, in one tree, graded and logged."""

import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import anthropic
from agent_tools.firefox.tools import bootstrap_firefox, build_firefox, run_tests
from agent_tools.firefox.tools.run_tests import TEST_DIRS, TEST_ROOTS
from hackbot_runtime import AgentError, HackbotContext

from . import prices, tree
from .grading import checks, tests
from .grading.judge import judge
from .isolation import example_env, run_isolated
from .weave_log import WeaveLog

log = logging.getLogger(__name__)

AGENTS = {"prompt-executor": "hackbot_agents.evaluator.prompt_executor"}
METRICS = (
    "test_progress",
    "quality",
    "builds",
    "integrity",
    "timed_out",
    "test_regressions",
)


@dataclass
class Experiment:
    dataset: str
    agent: str
    harness: str
    model: str
    effort: str | None
    reps: int
    example_timeout_min: int
    judge_model: str
    judge_rubric: str
    # What else sets this run apart, e.g. a prompt change; appended to the name.
    label: str | None = None

    @property
    def name(self) -> str:
        name = "/".join(
            [self.agent, self.harness, self.model, self.effort or "default"]
        )
        return f"{name}:{self.label}" if self.label else name


def _since(started: float) -> str:
    return f"{(time.monotonic() - started) / 60:.1f} min"


def summarize(records: list[dict], errors: int) -> dict:
    out: dict = {"examples": len(records), "errors": errors}
    for metric in METRICS:
        values = [r["scores"][metric] for r in records if metric in r["scores"]]
        if values:
            out[metric] = round(sum(values) / len(values), 4)
    costs = [r["cost_usd"] for r in records if r.get("cost_usd") is not None]
    if costs:
        out["cost_usd_total"] = round(sum(costs), 2)
    return out


class Runner:
    def __init__(self, exp: Experiment, ctx: HackbotContext, rows: list[dict]):
        self.exp, self.ctx, self.rows = exp, ctx, rows
        self.work = Path(tempfile.mkdtemp(prefix="work-"))
        self.judge = anthropic.AsyncAnthropic()
        import weave

        self.rubric = weave.ref(exp.judge_rubric).get().content

    async def build(self, src: Path) -> bool:
        started = time.monotonic()
        result = await build_firefox(src, tree.mozconfig(src), src / tree.OBJDIR)
        if not result.get("success"):
            output = (result.get("stdout") or "") + (result.get("stderr") or "")
            log.warning(
                "build failed after %s: %s\n%s",
                _since(started),
                result.get("message"),
                output[-3000:],
            )
            return False
        log.info("build done in %s", _since(started))
        return True

    async def run_tests(self, src: Path, row: dict, label: str) -> dict[str, dict]:
        """Every suite in the row's ``tests``, keyed ``<suite>:<test>``."""
        results: dict[str, dict] = {}
        for i, suite in enumerate(row.get("tests", [])):
            report = self.work / f"{label}-{row['id']}-{i}.json"
            ran = await run_tests(
                src, suite["harness"], suite["paths"], report, suite.get("prefs")
            )
            results.update({f"{i}:{test}": entry for test, entry in ran.items()})
        return results

    async def attempt(
        self, src: Path, base: str, row: dict, rep: int
    ) -> tuple[dict, str]:
        """Run the agent under test as a hackbot agent; its outcome and the diff.

        It shares this run's id, so its traces belong to the run, and writes its
        summary locally rather than over this run's uploaded one.
        """
        exp = self.exp
        ex_dir = self.work / f"{row['id']}-{rep}"
        parent = {k: v for k, v in os.environ.items() if not k.startswith("RESULTS_")}
        env = example_env(
            parent,
            ex_dir,
            {
                "PROMPT": row["prompt"],
                "HARNESS": exp.harness,
                "MODEL": exp.model,
                "EFFORT": exp.effort or "",
                "SOURCE_REPO": str(src),
                "RUN_ID": self.ctx.run_id,
                "ARTIFACTS_DIR": str(ex_dir / "artifacts"),
            },
        )
        code = await run_isolated(
            [sys.executable, "-m", AGENTS[exp.agent]],
            env,
            Path.cwd(),
            exp.example_timeout_min * 60,
            ex_dir / "agent.log",
            f"[{row['id']}] ",
        )
        patch = tree.diff(src, base)
        summary = ex_dir / "artifacts" / self.ctx.run_id / "summary.json"
        if code is None:
            # Out of time is an outcome, not a failure: what it left is graded.
            return {"timed_out": True}, patch
        if not summary.exists():
            return {
                "error": f"exited {code} without a summary",
                "error_class": "crash",
            }, patch
        result = json.loads(summary.read_text())
        out = result.get("findings") or {}
        if result.get("status") != "ok":
            out.update(error=result.get("error"), error_class="harness")
        elif out.get("error") and not patch.strip():
            out["error_class"] = "agent"
        elif out.get("models") and out["models"][0] != exp.model:
            out["error"] = f"served by {out['models']}, requested {exp.model}"
            out["error_class"] = "model_mismatch"
        return out, patch

    async def grade(
        self,
        src: Path,
        base: str,
        row: dict,
        rep: int,
        baseline: dict | None,
        patch: str,
    ) -> dict[str, float]:
        """Build, and run the row's tests with the test files as they were at base.

        Editing a harness's tests fails integrity; its metadata, such as the prefs
        a test directory runs with, is the agent's to change, but is restored too.
        """
        scores: dict[str, float] = {}
        harnesses = {suite["harness"] for suite in row.get("tests", [])}
        if harnesses:
            scores["integrity"] = float(
                not any(checks.touches(patch, TEST_DIRS[h]) for h in harnesses)
            )
            for harness in harnesses:
                tree.restore(src, base, TEST_ROOTS[harness])
        built = await self.build(src)
        scores["builds"] = float(built)
        if baseline is not None:
            if built:
                after = await self.run_tests(src, row, f"after{rep}")
                scores.update(tests.score(baseline, after))
            else:
                scores["test_progress"] = 0.0
        return scores

    async def run(self) -> dict:
        exp = self.exp
        src = await self.ctx.prepare_repo()
        base = tree.head(src)
        log.info(
            "%s on %d examples x %d reps, Firefox at %s",
            exp.name,
            len(self.rows),
            exp.reps,
            base[:12],
        )
        started = time.monotonic()
        log.info("bootstrapping the toolchain")
        bootstrap = await bootstrap_firefox(src)
        if not bootstrap.get("success"):
            raise AgentError(f"bootstrap failed: {bootstrap.get('message')}")
        log.info(
            "bootstrap done in %s; building Firefox (the first build is the long one)",
            _since(started),
        )
        if not await self.build(src):
            raise AgentError(f"could not build Firefox at {base}")
        baselines = {}
        for row in self.rows:
            if row.get("tests"):
                started = time.monotonic()
                baselines[row["id"]] = await self.run_tests(src, row, "base")
                if not baselines[row["id"]]:
                    logs = sorted(self.work.glob(f"base-{row['id']}-*.log"))
                    tail = logs[0].read_text()[-3000:] if logs else ""
                    raise AgentError(
                        f"no test results at the base for {row['id']}:\n{tail}"
                    )
                log.info(
                    "baseline %s: %d test files in %s",
                    row["id"],
                    len(baselines[row["id"]]),
                    _since(started),
                )
        weave_log = WeaveLog(
            exp.name, {**asdict(exp), "firefox_commit": base}, exp.dataset, self.rows
        )
        records, errors = [], 0

        for row in self.rows:
            for rep in range(exp.reps):
                tree.reset(src, base)
                pred = weave_log.start(row, row["id"], rep)
                started = time.monotonic()
                log.info(
                    "%s rep %d: agent running (limit %d min)",
                    row["id"],
                    rep,
                    exp.example_timeout_min,
                )
                out, patch = await self.attempt(src, base, row, rep)
                log.info(
                    "%s rep %d: agent finished in %s, %d lines changed, %s tool calls%s",
                    row["id"],
                    rep,
                    _since(started),
                    checks.lines_changed(patch),
                    out.get("tool_calls", "?"),
                    f", {out['error_class']}: {out['error']}"
                    if out.get("error_class")
                    else ", timed out"
                    if out.get("timed_out")
                    else "",
                )
                record = {"id": row["id"], "rep": rep}
                if out.get("error_class"):
                    errors += 1
                    record.update(error=out["error"], error_class=out["error_class"])
                else:
                    started = time.monotonic()
                    scores = await self.grade(
                        src, base, row, rep, baselines.get(row["id"]), patch
                    )
                    scores["timed_out"] = float(bool(out.get("timed_out")))
                    log.info(
                        "%s rep %d: graded in %s: %s",
                        row["id"],
                        rep,
                        _since(started),
                        scores,
                    )
                    try:
                        judgment, judge_model, judge_usage = await judge(
                            self.judge,
                            row["prompt"],
                            patch,
                            out.get("message", ""),
                            exp.judge_model,
                            self.rubric,
                        )
                        scores["quality"] = float(judgment.quality)
                        record.update(
                            judge=judgment.model_dump(),
                            judge_model=judge_model,
                            judge_usage=judge_usage,
                        )
                        log.info(
                            "%s rep %d: judge quality %d",
                            row["id"],
                            rep,
                            judgment.quality,
                        )
                    except Exception as exc:
                        record["judge_error"] = f"{type(exc).__name__}: {exc}"
                        log.warning("%s rep %d: judge failed: %s", row["id"], rep, exc)
                    record.update(
                        scores=scores,
                        cost_usd=out.get("cost_usd")
                        if out.get("cost_usd") is not None
                        else prices.cost(out.get("usage") or {}),
                        usage=out.get("usage"),
                        lines_changed=checks.lines_changed(patch),
                        **{
                            k: out.get(k)
                            for k in (
                                "num_turns",
                                "tool_calls",
                                "tool_errors",
                                "latency_s",
                            )
                        },
                    )
                    records.append(record)
                details = {
                    k: v for k, v in record.items() if k not in ("id", "rep", "scores")
                }
                weave_log.finish(
                    pred,
                    {"message": out.get("message", ""), "patch": patch, **details},
                    record.get("scores", {}),
                )
                self.publish(
                    f"{row['id']}_rep{rep}", record, patch, out.get("transcript", [])
                )

        summary = summarize(records, errors)
        # Weave averages the scores itself.
        weave_log.summary({k: v for k, v in summary.items() if k not in METRICS})
        log.info("done: %s; Weave: %s", summary, weave_log.url)
        return {
            "experiment": exp.name,
            "firefox_commit": base,
            "summary": summary,
            "weave_url": weave_log.url,
        }

    def publish(self, name: str, record: dict, patch: str, transcript: list) -> None:
        for key, content, kind in (
            (f"patches/{name}.diff", patch, "text/x-diff"),
            (
                f"transcripts/{name}.json",
                json.dumps(transcript, indent=1),
                "application/json",
            ),
        ):
            path = self.work / key.replace("/", "-")
            path.write_text(content)
            self.ctx.publish_file(key, path, kind)
        rows = self.work / "rows.jsonl"
        with rows.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        self.ctx.publish_file("rows.jsonl", rows, "application/x-ndjson")
