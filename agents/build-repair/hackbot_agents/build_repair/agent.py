# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Build-repair agent.

Two-stage claude-agent-sdk agent that analyzes a Firefox build failure and
implements a fix in the source tree. The runtime checks the tree out at the
failure commit (via ``SOURCE_REF``) and collects the agent's edits into
``changes.patch``; this module only orchestrates the agent and publishes the
analysis artifacts.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from hackbot_agents.build_repair.logs import download_failure_logs
from hackbot_agents.build_repair.try_push import TRY_TOOLS
from hackbot_runtime import AgentError, HackbotAgentResult
from hackbot_runtime.claude import Reporter

from .config import (
    ADDITIONAL_DIRS,
    ALLOWED_TOOLS,
    ANALYSIS_MODEL,
    BUGZILLA_READ_TOOLS,
    BUILD_TOOL,
    FIREFOX_TOOLS,
    FIX_MODEL,
    TRY_PUSH_TOOL,
)
from .prompts import (
    ANALYSIS_TEMPLATE,
    BUG_ANALYSIS_STEP,
    BUG_CONTEXT,
    FIX_TEMPLATE,
    TRY_PUSH_INSTRUCTIONS,
)

TARGET_SOFTWARE = "Mozilla Firefox"


class BuildRepairResult(HackbotAgentResult):
    bug_id: int | None = None
    git_commit: str
    summary: str = ""
    analysis: str = ""
    local_build_verified: bool | None = None
    try_build_passed: bool | None = None
    lando_job_id: str | None = None
    treeherder_url: str | None = None


def _result_text(block: ToolResultBlock) -> str:
    if isinstance(block.content, str):
        return block.content
    if isinstance(block.content, list):
        return "\n".join(
            c.get("text", "")
            for c in block.content
            if isinstance(c, dict) and c.get("type") == "text"
        )
    return str(block.content)


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
    # The agent always runs inside an isolated Docker container, so there is no
    # sandbox and tools run without per-command permission prompts.
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


def _write_mozconfig(fx_ctx: FirefoxContext) -> None:
    """Write a mozconfig mirroring the failing CI build, unless one exists.

    Verification only means something if the local build reproduces the failure
    condition. Many failures (e.g. a variable used only inside a stripped
    ``MOZ_DIAGNOSTIC_ASSERT``) compile fine in a default Nightly-style build and
    fail only in a release-milestone build with warnings-as-errors. ``--enable-
    release`` leaves ``MOZ_DIAGNOSTIC_ASSERT_ENABLED`` undefined and
    ``--enable-warnings-as-errors`` promotes warnings to errors, so this config
    surfaces that whole class locally.
    """
    if fx_ctx.mozconfig.exists():
        return
    fx_ctx.mozconfig.write_text(
        "ac_add_options --enable-application=browser\n"
        "ac_add_options --disable-debug\n"
        "ac_add_options --enable-release\n"
        "ac_add_options --enable-warnings-as-errors\n"
        f"mk_add_options MOZ_OBJDIR={fx_ctx.objdir}\n"
    )


async def run_build_repair(
    *,
    bugzilla_mcp_server: McpServerConfig,
    source_repo: Path,
    fx_ctx: FirefoxContext,
    bug_id: int | None = None,
    git_commit: str,
    failure_tasks: dict[str, str],
    run_try_push: bool = False,
    model: str | None = None,
    max_turns: int | None = None,
    verbose: bool = False,
    log: Path | None = None,
    publish_file: Callable[[str, Path, str | None], str] | None = None,
) -> BuildRepairResult:
    """Analyze a build failure and implement a fix in ``source_repo``.

    Returns a :class:`BuildRepairResult`; raises :class:`AgentError` if a stage
    ends in an error or produces no result.
    """
    label = f"bug {bug_id}" if bug_id is not None else f"commit {git_commit[:12]}"
    print(f"[build_repair] repairing {label} at {git_commit}", file=sys.stderr)

    scratch_dir = Path(tempfile.mkdtemp(prefix=f"build-repair-{bug_id or 'nobug'}-"))
    scratch_in = scratch_dir / "in"
    scratch_out = scratch_dir / "out"
    scratch_in.mkdir(parents=True, exist_ok=True)
    scratch_out.mkdir(parents=True, exist_ok=True)

    task_logs = await download_failure_logs(failure_tasks, scratch_in)
    failure_logs = "\n".join(
        f"- {name}: sanitized errors at {tl.sanitized} (start here); "
        f"full log at {tl.full}"
        for name, tl in task_logs.items()
    )

    firefox_tools = [*firefox.TOOLS, *TRY_TOOLS] if run_try_push else firefox.TOOLS
    firefox_server = build_sdk_server("firefox", fx_ctx, firefox_tools)
    mcp_servers: dict[str, McpServerConfig] = {
        "bugzilla": bugzilla_mcp_server,
        "firefox": firefox_server,
    }
    allowed_tools = [
        *ALLOWED_TOOLS,
        *BUGZILLA_READ_TOOLS,
        *FIREFOX_TOOLS,
        *([TRY_PUSH_TOOL] if run_try_push else []),
    ]

    task_name = next(iter(failure_tasks), "")
    analysis_prompt = ANALYSIS_TEMPLATE.format(
        target_software=TARGET_SOFTWARE,
        git_commit=git_commit,
        failure_logs=failure_logs,
        scratch_out=scratch_out,
        bug_context=BUG_CONTEXT.format(bug_id=bug_id) if bug_id is not None else "",
        bug_step=BUG_ANALYSIS_STEP.format(bug_id=bug_id) if bug_id is not None else "",
        logs_num=3 if bug_id is not None else 2,
    )
    fix_prompt = FIX_TEMPLATE.format(
        target_software=TARGET_SOFTWARE,
        scratch_out=scratch_out,
        try_push=(
            TRY_PUSH_INSTRUCTIONS.format(task_name=task_name) if run_try_push else ""
        ),
    )

    total_cost = 0.0
    total_turns = 0
    # Last JSON result of each tracked tool, keyed by tool name. Lets us report
    # the actual local-build / try-push outcomes instead of guessing.
    captured: dict[str, dict] = {}
    tracked = {BUILD_TOOL, *([TRY_PUSH_TOOL] if run_try_push else [])}

    with Reporter(verbose=verbose, log_path=log) as reporter:
        # Stage 1: analysis (high effort, no source edits yet).
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
        result_msg = await _run_session(
            reporter, analysis_opts, analysis_prompt, captured, tracked
        )
        _check(result_msg, label, "analysis")
        total_cost += result_msg.total_cost_usd or 0.0
        total_turns += result_msg.num_turns or 0

        # Stage 2: fix (lower effort, edits the source tree and verifies it
        # builds against a mozconfig that mirrors the failing CI config).
        _write_mozconfig(fx_ctx)
        reporter.header(f"{label}: fix")
        fix_opts = _build_options(
            model=model or FIX_MODEL,
            effort="low",
            cwd=source_repo,
            scratch_dir=scratch_dir,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
        )
        result_msg = await _run_session(
            reporter, fix_opts, fix_prompt, captured, tracked
        )
        _check(result_msg, label, "fix")
        total_cost += result_msg.total_cost_usd or 0.0
        total_turns += result_msg.num_turns or 0

    summary = _read_doc(scratch_out, "summary", publish_file)
    analysis = _read_doc(scratch_out, "analysis", publish_file)

    build_result = captured.get(BUILD_TOOL)
    try_result = captured.get(TRY_PUSH_TOOL, {})

    return BuildRepairResult(
        bug_id=bug_id,
        git_commit=git_commit,
        summary=summary,
        analysis=analysis,
        local_build_verified=build_result.get("success") if build_result else None,
        try_build_passed=try_result.get("try_build_passed"),
        lando_job_id=try_result.get("lando_job_id"),
        treeherder_url=try_result.get("treeherder_url"),
        num_turns=total_turns,
        total_cost_usd=total_cost,
    )


async def _run_session(
    reporter: Reporter,
    options: ClaudeAgentOptions,
    prompt: str,
    captured: dict[str, dict],
    tracked: set[str],
) -> ResultMessage | None:
    """Drive one agent session, capturing the last result of each tracked tool.

    ``captured`` is keyed by tool name and updated in place with the parsed JSON
    of each successful call to a tool in ``tracked`` (e.g. the local build and
    the try push), so the caller can report real outcomes.
    """
    pending: dict[str, str] = {}
    result_msg: ResultMessage | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            reporter.message(msg)
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock) and block.name in tracked:
                        pending[block.id] = block.name
            elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                for block in msg.content:
                    if (
                        isinstance(block, ToolResultBlock)
                        and block.tool_use_id in pending
                        and not block.is_error
                    ):
                        name = pending.pop(block.tool_use_id)
                        try:
                            captured[name] = json.loads(_result_text(block))
                        except (ValueError, TypeError):
                            pass
            elif isinstance(msg, ResultMessage):
                result_msg = msg
    return result_msg


def _check(result_msg: ResultMessage | None, label: str, stage: str) -> None:
    if result_msg is None:
        raise AgentError(f"{label}: {stage} stage produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"{label}: {stage} stage failed: {result_msg.result or result_msg.subtype}"
        )


def _read_doc(
    scratch_out: Path,
    key: str,
    publish_file: Callable[[str, Path, str | None], str] | None,
) -> str:
    """Read a stage-1 output doc and, if a publisher is given, publish it."""
    path = scratch_out / f"{key}.md"
    if not path.exists():
        return ""
    if publish_file is not None:
        publish_file(f"{key}.md", path, "text/markdown")
    return path.read_text()
