# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Test-repair agent for Firefox CI test failures.

Blame the commit that regressed a failing test and propose a fix. The pulse
listener only forwards failures that already passed its regression and flakiness
filters, so a regression is the prior, but the agent still reports the
classification it reaches from the logs.

A two-stage claude-agent-sdk loop. Stage 1 (analysis, read-only) inspects the
candidate commit diffs and writes a verdict naming the culprit; Stage 2 (fix)
runs when a culprit is found and proposes a source patch, which the runtime
collects into ``changes.patch``. The :class:`TestRepairResult` is serialized into
``summary.json``'s ``findings`` and read by the notifier.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
from agent_tools.firefox.tools import bootstrap_firefox
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import AgentError, HackbotAgentResult
from hackbot_runtime.claude import Reporter

from .config import (
    ADDITIONAL_DIRS,
    ALLOWED_TOOLS,
    ANALYSIS_MODEL,
    BUGZILLA_READ_TOOLS,
    FIREFOX_TOOLS,
    FIX_MODEL,
)
from .logs import TaskLogs
from .prompts import (
    ANALYSIS_TEMPLATE,
    CANDIDATE_INTRO_COMPLETE,
    CANDIDATE_INTRO_PARTIAL,
    ENVIRONMENT_NOTE,
    FIX_TEMPLATE,
    LAST_GREEN_LINE,
    MAX_CANDIDATE_COMMITS,
    MAX_TESTS_PER_GROUP,
    VERIFY_LOCAL,
    VERIFY_REMOTE,
)
from .resolve import CommitRange, Investigation

_CLASSIFICATIONS = ("regression", "intermittent")
_RECOMMENDATIONS = ("backout", "do_not_backout", "land_fix", "rerun")
_SANITIZER_OPTIONS = {"asan": "address-sanitizer", "tsan": "thread-sanitizer"}


class TestRepairResult(HackbotAgentResult):
    classification: Literal["regression", "intermittent"]
    recommendation: Literal["backout", "do_not_backout", "land_fix", "rerun"]
    culprit_commit: str | None = None
    # Ranked commits that could not be ruled out, when no single culprit convinced
    # the agent; lets sheriffs retrigger just these instead of backfilling.
    candidate_commits: list[str] = []
    culprit_bug: int | None = None
    confidence: float = 0.0
    last_green_revision: str | None = None
    intermittent_bug: int | None = None
    proposed_patch: bool = False
    summary: str = ""
    analysis: str = ""


def _build_options(
    *,
    model: str | None,
    effort: str,
    cwd: Path,
    scratch_dir: Path,
    mcp_servers: dict[str, McpServerConfig],
    allowed_tools: list[str],
    max_turns: int | None,
) -> ClaudeAgentOptions:
    # The agent runs inside an isolated container, so tools run without
    # per-command permission prompts.
    return ClaudeAgentOptions(
        model=model,
        cwd=str(cwd),
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        disallowed_tools=["AskUserQuestion", "Task"],
        add_dirs=[*ADDITIONAL_DIRS, str(scratch_dir)],
        permission_mode="bypassPermissions",
        effort=effort,
        max_turns=max_turns,
        setting_sources=[],
    )


async def _run_session(
    reporter: Reporter, options: ClaudeAgentOptions, prompt: str
) -> ResultMessage | None:
    result_msg: ResultMessage | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            reporter.message(msg)
            if isinstance(msg, ResultMessage):
                result_msg = msg
    return result_msg


def _check(result_msg: ResultMessage | None, stage: str) -> None:
    if result_msg is None:
        raise AgentError(f"{stage} stage produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"{stage} stage failed: {result_msg.result or result_msg.subtype}"
        )


def _read_doc(
    scratch_out: Path,
    key: str,
    publish_file: Callable[[str, Path, str | None], str] | None,
) -> str:
    path = scratch_out / f"{key}.md"
    if not path.exists():
        return ""
    if publish_file is not None:
        publish_file(f"{key}.md", path, "text/markdown")
    return path.read_text()


def _read_verdict(scratch_out: Path) -> dict:
    path = scratch_out / "verdict.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return {}


def _coerce_classification(value) -> str:
    return value if value in _CLASSIFICATIONS else "regression"


def _coerce_recommendation(value, classification: str, has_culprit: bool) -> str:
    if value not in _RECOMMENDATIONS:
        value = "backout" if classification == "regression" else "do_not_backout"
    if value == "backout" and not has_culprit:
        return "do_not_backout"
    return value


def _resolve_culprit(source_repo: Path, sha) -> str | None:
    """Normalize a model-authored sha to a full commit hash in the checkout.

    The shallow clone holds exactly the candidate range, so a sha git cannot
    resolve there was invented or is out of range; either way it is dropped.
    """
    if not isinstance(sha, str) or not sha.strip():
        return None
    sha = sha.strip()
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(source_repo),
                "rev-parse",
                "--verify",
                f"{sha}^{{commit}}",
            ],
            capture_output=True,
            text=True,
        )
        full = proc.stdout.strip() if proc.returncode == 0 else ""
    except OSError:
        full = ""
    if not full:
        print(f"[test-repair] discarding culprit {sha!r}", file=sys.stderr)
        return None
    return full


def _resolve_candidates(source_repo: Path, value, culprit: str | None) -> list[str]:
    """Validate the model's ranked fallback candidates, preserving their order.

    Same discard rule as the culprit: a sha git cannot resolve in the shallow clone
    is not in the range. The culprit is dropped so the list stays a strict
    alternative to it rather than repeating it.
    """
    if not isinstance(value, list):
        return []
    resolved: list[str] = []
    for sha in value[: MAX_CANDIDATE_COMMITS * 2]:
        full = _resolve_culprit(source_repo, sha)
        if full and full != culprit and full not in resolved:
            resolved.append(full)
        if len(resolved) == MAX_CANDIDATE_COMMITS:
            break
    return resolved


def _write_mozconfig(fx_ctx: FirefoxContext, investigation: Investigation) -> None:
    """Write a mozconfig mirroring the failing CI build.

    ``build_firefox`` fails outright without one. The variant is mirrored because
    a plain build cannot trigger an assertion, sanitizer or coverage failure.

    Always overwritten: ``/workspace`` is a persistent volume shared with the
    other agents, so a leftover mozconfig from a previous run points the build at
    a foreign objdir with foreign flags.
    """
    options = ["ac_add_options --enable-application=browser"]
    if investigation.debug_build:
        options.append("ac_add_options --enable-debug")
    else:
        options += [
            "ac_add_options --disable-debug",
            "ac_add_options --enable-optimize",
        ]
    if investigation.sanitizer:
        options += [
            f"ac_add_options --enable-{_SANITIZER_OPTIONS[investigation.sanitizer]}",
            "ac_add_options --disable-jemalloc",
        ]
    if investigation.coverage_build:
        options.append("ac_add_options --enable-coverage")
    options.append(f"mk_add_options MOZ_OBJDIR={fx_ctx.objdir}")
    fx_ctx.mozconfig.write_text("\n".join(options) + "\n")


async def _bootstrap(fx_ctx: FirefoxContext) -> None:
    """Install the build toolchain before the fix stage needs it.

    Deterministic prep rather than a tool call: it is slow and unconditional, so
    spending agent turns deciding to run it wastes budget. Idempotent, and a
    failure here is not fatal -- build_firefox reports its own errors.
    """
    result = await bootstrap_firefox(fx_ctx.source_dir)
    if not result.get("success"):
        print(f"[test-repair] bootstrap: {result.get('message')}", file=sys.stderr)


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _assemble_result(
    scratch_out: Path,
    *,
    verdict: dict,
    source_repo: Path,
    last_green_revision: str | None,
    total_turns: int,
    total_cost: float,
    publish_file: Callable[[str, Path, str | None], str] | None,
) -> TestRepairResult:
    classification = _coerce_classification(verdict.get("classification"))
    culprit_commit = _resolve_culprit(source_repo, verdict.get("culprit_commit"))
    recommendation = _coerce_recommendation(
        verdict.get("recommendation"), classification, bool(culprit_commit)
    )
    return TestRepairResult(
        classification=classification,
        recommendation=recommendation,
        culprit_commit=culprit_commit,
        candidate_commits=_resolve_candidates(
            source_repo, verdict.get("candidate_commits"), culprit_commit
        ),
        culprit_bug=_as_int(verdict.get("culprit_bug")),
        intermittent_bug=_as_int(verdict.get("intermittent_bug")),
        confidence=_as_float(verdict.get("confidence")),
        last_green_revision=last_green_revision,
        proposed_patch=bool(verdict.get("proposed_patch")),
        summary=_read_doc(scratch_out, "summary", publish_file),
        analysis=_read_doc(scratch_out, "analysis", publish_file),
        num_turns=total_turns,
        total_cost_usd=total_cost,
    )


def _range_expr(commit_range: CommitRange) -> str:
    """A git revision range for ``git log``, falling back to the clone depth."""
    if commit_range.base:
        return f"{commit_range.base}..{commit_range.head}"
    return f"HEAD~{commit_range.span}..HEAD"


def _failing_tests(investigation: Investigation) -> str:
    groups = investigation.failing_groups
    if not groups:
        if not investigation.group_based:
            return (
                "- (this suite does not report test manifests; identify the failing"
                " tests from the failure logs)"
            )
        return "- (failing groups could not be resolved; identify them from the logs)"
    lines = []
    for group in groups:
        shown = group.tests[:MAX_TESTS_PER_GROUP]
        extra = len(group.tests) - len(shown)
        tests = ", ".join(shown) + (f", +{extra} more" if extra > 0 else "")
        count = f"{len(group.tests)} failed" if group.tests else "tests not resolved"
        lines.append(f"- {group.group} ({count}): {tests or 'n/a'}")
    return "\n".join(lines)


async def run_test_repair(
    *,
    bugzilla_mcp_server: McpServerConfig | None,
    source_repo: Path,
    fx_ctx: FirefoxContext,
    investigation: Investigation,
    task_logs: dict[str, TaskLogs],
    scratch_out: Path,
    model: str | None = None,
    max_turns: int | None = None,
    verbose: bool = False,
    log: Path | None = None,
    publish_file: Callable[[str, Path, str | None], str] | None = None,
) -> TestRepairResult:
    """Blame the commit that regressed a failing test and propose a fix."""
    commit_range = investigation.commit_range
    failure_commit = investigation.failure_commit
    print(
        f"[test-repair] analyzing {investigation.hg_revision} at {failure_commit}",
        file=sys.stderr,
    )

    firefox_server = build_sdk_server("firefox", fx_ctx, firefox.TOOLS)
    mcp_servers: dict[str, McpServerConfig] = {"firefox": firefox_server}
    allowed_tools = [*ALLOWED_TOOLS, *FIREFOX_TOOLS]
    # Bugzilla is optional context (searching for a related bug); wire it only
    # when a broker URL is provided.
    if bugzilla_mcp_server:
        mcp_servers["bugzilla"] = bugzilla_mcp_server
        allowed_tools += BUGZILLA_READ_TOOLS

    failure_logs = "\n".join(
        f"- {name}: sanitized failures at {tl.sanitized} (start here); "
        f"full log at {tl.full}"
        for name, tl in task_logs.items()
    )
    last_green_line = (
        LAST_GREEN_LINE.format(last_green_revision=investigation.last_green_revision)
        if investigation.last_green_revision
        else ""
    )
    range_expr = _range_expr(commit_range)
    intro = (
        CANDIDATE_INTRO_COMPLETE if commit_range.complete else CANDIDATE_INTRO_PARTIAL
    )
    analysis_prompt = ANALYSIS_TEMPLATE.format(
        failing_tests=_failing_tests(investigation),
        harness=investigation.harness,
        platform=investigation.platform or "unknown",
        label=investigation.label or "unknown",
        failure_commit=failure_commit,
        candidate_intro=intro.format(commit_range=range_expr, span=commit_range.span),
        commit_range=range_expr,
        max_candidates=MAX_CANDIDATE_COMMITS,
        last_green_line=last_green_line,
        failure_logs=failure_logs,
        scratch_out=scratch_out,
    )

    total_cost = 0.0
    total_turns = 0
    scratch_dir = scratch_out.parent

    label = (
        investigation.failing_groups[0].group
        if investigation.failing_groups
        else investigation.hg_revision[:12]
    )
    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(f"{label}: analysis")
        analysis_opts = _build_options(
            model=model or ANALYSIS_MODEL,
            effort="high",
            cwd=source_repo,
            scratch_dir=scratch_dir,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
        )
        result_msg = await _run_session(reporter, analysis_opts, analysis_prompt)
        _check(result_msg, "analysis")
        total_cost += result_msg.total_cost_usd or 0.0
        total_turns += result_msg.num_turns or 0

        # Stage 2 (fix) runs only when the analysis blamed a real commit.
        verdict = _read_verdict(scratch_out)
        culprit_commit = _resolve_culprit(source_repo, verdict.get("culprit_commit"))
        if culprit_commit:
            reporter.header(f"{label}: fix")
            _write_mozconfig(fx_ctx, investigation)
            await _bootstrap(fx_ctx)
            template = VERIFY_LOCAL if investigation.is_linux else VERIFY_REMOTE
            verify_step = template.format(
                harness=investigation.harness, platform=investigation.platform
            ) + ENVIRONMENT_NOTE.format(platform=investigation.platform)
            fix_prompt = FIX_TEMPLATE.format(
                culprit_commit=culprit_commit,
                verify_step=verify_step,
                scratch_out=scratch_out,
            )
            fix_opts = _build_options(
                model=model or FIX_MODEL,
                effort="low",
                cwd=source_repo,
                scratch_dir=scratch_dir,
                mcp_servers=mcp_servers,
                allowed_tools=allowed_tools,
                max_turns=max_turns,
            )
            # A failed fix stage must not discard the analysis we already paid for.
            try:
                result_msg = await _run_session(reporter, fix_opts, fix_prompt)
                if result_msg is not None:
                    total_cost += result_msg.total_cost_usd or 0.0
                    total_turns += result_msg.num_turns or 0
                _check(result_msg, "fix")
            except Exception as exc:
                print(f"[test-repair] fix stage failed: {exc}", file=sys.stderr)
            # Merge, since the fix stage may rewrite verdict.json without the culprit.
            fix_verdict = _read_verdict(scratch_out)
            verdict = {
                **verdict,
                **{k: v for k, v in fix_verdict.items() if v is not None},
            }

    return _assemble_result(
        scratch_out,
        verdict=verdict,
        source_repo=source_repo,
        last_green_revision=investigation.last_green_revision,
        total_turns=total_turns,
        total_cost=total_cost,
        publish_file=publish_file,
    )
