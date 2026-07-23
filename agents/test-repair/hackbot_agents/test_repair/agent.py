# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Test-repair agent for Firefox CI test failures.

Blame the commit that regressed a failing test and propose a fix. The pulse
listener only forwards failures that already passed its regression and flakiness
filters, so the agent assumes a genuine regression and does not re-classify.

A two-stage claude-agent-sdk loop. Stage 1 (analysis, read-only) inspects the
candidate commit diffs and writes a verdict naming the culprit; Stage 2 (fix)
runs when a culprit is found and proposes a source patch, which the runtime
collects into ``changes.patch``. The :class:`TestRepairResult` is
serialized into ``summary.json``'s ``findings`` and read by the notifier.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
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
    FIX_TEMPLATE,
    LAST_GREEN_LINE,
)
from .resolve import FailingGroup, Investigation

_CLASSIFICATIONS = ("regression", "intermittent")
_RECOMMENDATIONS = ("backout", "do_not_backout", "land_fix")


class TestRepairResult(HackbotAgentResult):
    classification: Literal["regression", "intermittent"]
    recommendation: Literal["backout", "do_not_backout", "land_fix"]
    culprit_commit: str | None = None
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


def _coerce_recommendation(value, classification: str) -> str:
    if value in _RECOMMENDATIONS:
        return value
    return "backout" if classification == "regression" else "do_not_backout"


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
    last_green_revision: str | None,
    total_turns: int,
    total_cost: float,
    publish_file: Callable[[str, Path, str | None], str] | None,
) -> TestRepairResult:
    verdict = _read_verdict(scratch_out)
    classification = _coerce_classification(verdict.get("classification"))
    recommendation = _coerce_recommendation(
        verdict.get("recommendation"), classification
    )
    culprit_commit = verdict.get("culprit_commit")
    return TestRepairResult(
        classification=classification,
        recommendation=recommendation,
        culprit_commit=culprit_commit or None,
        culprit_bug=_as_int(verdict.get("culprit_bug")),
        confidence=_as_float(verdict.get("confidence")),
        last_green_revision=last_green_revision,
        proposed_patch=bool(verdict.get("proposed_patch")),
        summary=_read_doc(scratch_out, "summary", publish_file),
        analysis=_read_doc(scratch_out, "analysis", publish_file),
        num_turns=total_turns,
        total_cost_usd=total_cost,
    )


def _commit_lines(candidate_commits: list[str]) -> str:
    return "\n".join(f"- {c}" for c in candidate_commits)


def _failing_tests(groups: list[FailingGroup]) -> str:
    if not groups:
        return "- (failing groups could not be resolved; identify them from the logs)"
    return "\n".join(f"- {g.group} (e.g. {g.test})" for g in groups)


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
    candidate_commits = investigation.candidate_commits
    if not candidate_commits:
        raise AgentError("candidate_commits must contain at least the head commit")
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
    analysis_prompt = ANALYSIS_TEMPLATE.format(
        failing_tests=_failing_tests(investigation.failing_groups),
        harness=investigation.harness,
        failure_commit=failure_commit,
        commit_lines=_commit_lines(candidate_commits),
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

        # Stage 2 (fix) runs when the analysis identified a culprit commit.
        verdict = _read_verdict(scratch_out)
        culprit_commit = verdict.get("culprit_commit")
        if culprit_commit:
            reporter.header(f"{label}: fix")
            fix_prompt = FIX_TEMPLATE.format(
                culprit_commit=culprit_commit,
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
            result_msg = await _run_session(reporter, fix_opts, fix_prompt)
            _check(result_msg, "fix")
            total_cost += result_msg.total_cost_usd or 0.0
            total_turns += result_msg.num_turns or 0

    return _assemble_result(
        scratch_out,
        last_green_revision=investigation.last_green_revision,
        total_turns=total_turns,
        total_cost=total_cost,
        publish_file=publish_file,
    )
