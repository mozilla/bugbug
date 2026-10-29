"""Perftest-repair agent for intermittent and permanent perf test failures.

Given a bug for a failing perf test job (mozperftest, raptor/browsertime, talos or
AWSY), the agent reads its failure logs and works out whether the test, harness,
Firefox or infrastructure is at fault. With a confident fix it leaves the change
in the working tree and records a try push of it with retriggers.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, get_args

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
from agent_tools.firefox.tools import bootstrap_firefox
from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import ActionsRecorder, AgentError, HackbotAgentResult
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.claude_sdk import actions_server_for, actions_to_tool_names
from hackbot_runtime.claude import Reporter
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .config import (
    ACTIONS,
    ADDITIONAL_DIRS,
    ALLOWED_TOOLS,
    BUGZILLA_READ_TOOLS,
    EFFORT,
    FIREFOX_TOOLS,
    MODEL,
    SKIP_FIREFOX_BUILD,
    SOURCE_WRITE_TOOLS,
    TRY_REBUILD_INTERMITTENT,
    TRY_REBUILD_PERMAFAIL,
    TRY_REF,
)
from .recent import RECENT_DAYS, recent_failures

HERE = Path(__file__).resolve().parent
PROMPTS = HERE / "prompts"
RULES = HERE / "rules"

FailureType = Literal["permafail", "intermittent", "unknown"]
Harness = Literal["mozperftest", "raptor", "talos", "awsy", "other"]
Cause = Literal["test", "harness", "firefox", "infra", "unknown"]
Outcome = Literal["fix", "diagnosis", "skipped"]


class PerftestRepairResult(HackbotAgentResult):
    bug_id: int
    failure_type: FailureType = "unknown"
    harness: Harness = "other"
    cause: Cause = "unknown"
    # Set from the working tree, not the model: "fix" if a change was left behind.
    # "skipped" means the bug had no recent failures and no session ran.
    outcome: Outcome = "diagnosis"
    confidence: float = 0.0
    regressor_bug: int | None = None
    summary: str = ""
    analysis: str = ""


def render_prompt(name: str, **fields: object) -> str:
    """Render a prompt template from ``prompts/`` via ``str.format``."""
    return (PROMPTS / name).read_text().format(**fields).strip()


def make_investigator() -> AgentDefinition:
    """A read-only subagent the main agent briefs for each focused question."""
    return AgentDefinition(
        description=(
            "Focused investigator for one specific question about the failure, "
            "a log, or the source tree. The main agent writes your complete "
            "instructions at spawn time -- follow them and return only what was "
            "asked for."
        ),
        prompt=(
            "You are a focused investigator subagent. Complete the self-contained "
            "task you are given and return a concise answer. You have read-only "
            "access. Do not speculate beyond what you can verify."
        ),
        tools=["Read", "Grep", "Glob", "Bash", *BUGZILLA_READ_TOOLS],
        model="inherit",
    )


def _write_mozconfig(fx_ctx: FirefoxContext) -> None:
    """An optimized desktop build, which is what perf tests measure."""
    fx_ctx.mozconfig.write_text(
        "ac_add_options --enable-application=browser\n"
        "ac_add_options --disable-debug\n"
        "ac_add_options --enable-optimize\n"
        f"mk_add_options MOZ_OBJDIR={fx_ctx.objdir}\n"
    )


async def _bootstrap(fx_ctx: FirefoxContext) -> None:
    """Install the build toolchain. Idempotent; a failure is not fatal."""
    result = await bootstrap_firefox(fx_ctx.source_dir)
    if not result.get("success"):
        print(f"[perftest-repair] bootstrap: {result.get('message')}", file=sys.stderr)


def _choice(value, allowed: tuple[str, ...], default: str) -> str:
    return value if value in allowed else default


def _as_float(value) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except TypeError, ValueError:
        return 0.0


def _as_int(value) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _read_verdict(scratch_out: Path) -> dict:
    try:
        verdict = json.loads((scratch_out / "verdict.json").read_text())
    except ValueError, OSError:
        return {}
    return verdict if isinstance(verdict, dict) else {}


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


def _outcome(source_repo: Path | None) -> Outcome:
    if source_repo is None:
        return "diagnosis"
    changed = subprocess.run(
        ["git", "-C", str(source_repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return "fix" if changed else "diagnosis"


# The broker sidecar can start a moment after the agent, so allow it a short grace.
BROKER_ATTEMPTS = 5
BROKER_RETRY_SECONDS = 3.0


async def check_broker(bugzilla_mcp_server: McpServerConfig, bug: int) -> None:
    """Fail the run unless the Bugzilla broker serves the bug.

    Without it the agent falls back to the public REST API, which cannot read
    restricted bugs.
    """
    url = (
        bugzilla_mcp_server.get("url")
        if isinstance(bugzilla_mcp_server, dict)
        else None
    )
    if not url:
        raise AgentError("no Bugzilla broker URL configured")

    error: Exception | None = None
    for attempt in range(BROKER_ATTEMPTS):
        if attempt:
            await asyncio.sleep(BROKER_RETRY_SECONDS)
        try:
            async with (
                streamablehttp_client(url) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                res = await session.call_tool(
                    "get_bugs", {"ids": [bug], "include_fields": "id"}
                )
            if res.isError:
                raise RuntimeError("".join(getattr(c, "text", "") for c in res.content))
            return
        except Exception as e:  # retried, then reported below
            error = e
    raise AgentError(
        f"Bugzilla broker at {url} is unavailable after {BROKER_ATTEMPTS} attempts: "
        f"{error}. Check that the broker container started and has its "
        "BUGZILLA_API_URL and BUGZILLA_API_KEY."
    )


def assemble_result(
    *,
    bug: int,
    scratch_out: Path,
    result_msg: ResultMessage,
    source_repo: Path | None = None,
    publish_file: Callable[[str, Path, str | None], str] | None = None,
) -> PerftestRepairResult:
    verdict = _read_verdict(scratch_out)
    return PerftestRepairResult(
        bug_id=bug,
        failure_type=_choice(
            verdict.get("failure_type"), get_args(FailureType), "unknown"
        ),
        harness=_choice(verdict.get("harness"), get_args(Harness), "other"),
        cause=_choice(verdict.get("cause"), get_args(Cause), "unknown"),
        outcome=_outcome(source_repo),
        confidence=_as_float(verdict.get("confidence")),
        regressor_bug=_as_int(verdict.get("regressor_bug")),
        summary=_read_doc(scratch_out, "summary", publish_file),
        analysis=_read_doc(scratch_out, "analysis", publish_file),
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
    )


async def run_perftest_repair(
    *,
    bugzilla_mcp_server: McpServerConfig,
    source_repo: Path,
    fx_ctx: FirefoxContext,
    bug: int,
    scratch_out: Path,
    skip_firefox_build: bool = SKIP_FIREFOX_BUILD,
    rules_dir: Path = RULES,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
    actions_recorder: ActionsRecorder | None = None,
    ignore_stop_conditions: bool = False,
    publish_file: Callable[[str, Path, str | None], str] | None = None,
) -> PerftestRepairResult:
    """Diagnose a failing perf test bug and propose a fix."""
    print(f"[perftest-repair] investigating bug {bug}", file=sys.stderr)
    if not ignore_stop_conditions:
        failures = await recent_failures(bug)
        if failures == 0:
            print(
                f"[perftest-repair] bug {bug}: no failures in the last {RECENT_DAYS} "
                "days; skipping until it fails again",
                file=sys.stderr,
            )
            return PerftestRepairResult(
                bug_id=bug,
                outcome="skipped",
                summary=f"No failures in the last {RECENT_DAYS} days; skipped.",
                num_turns=0,
                total_cost_usd=0.0,
            )
    await check_broker(bugzilla_mcp_server, bug)

    actions_recorder, actions_server = actions_server_for(
        actions_recorder, types=ACTIONS
    )
    mcp_servers: dict[str, McpServerConfig] = {
        "bugzilla": bugzilla_mcp_server,
        ACTIONS_SERVER_NAME: actions_server,
    }
    allowed_tools = [
        *ALLOWED_TOOLS,
        *SOURCE_WRITE_TOOLS,
        *BUGZILLA_READ_TOOLS,
        *actions_to_tool_names(ACTIONS),
    ]

    if skip_firefox_build:
        verify_step = render_prompt("verify-skipped.md")
    else:
        _write_mozconfig(fx_ctx)
        await _bootstrap(fx_ctx)
        mcp_servers["firefox"] = build_sdk_server("firefox", fx_ctx, firefox.TOOLS)
        allowed_tools += FIREFOX_TOOLS
        verify_step = render_prompt("verify-local.md")

    rules_path = str(rules_dir.resolve())
    options = ClaudeAgentOptions(
        system_prompt=render_prompt(
            "system.md",
            rules_dir=rules_path,
            source_dir=str(source_repo.resolve()),
        ),
        mcp_servers=mcp_servers,
        agents={"investigator": make_investigator()},
        cwd=str(source_repo.resolve()),
        add_dirs=[*ADDITIONAL_DIRS, rules_path, str(scratch_out.parent)],
        permission_mode="bypassPermissions",
        allowed_tools=allowed_tools,
        disallowed_tools=["AskUserQuestion"],
        model=model or MODEL,
        max_turns=max_turns,
        effort=effort or EFFORT,
        setting_sources=[],
    )
    prompt = render_prompt(
        "investigate-and-fix.md",
        bug_id=bug,
        rules_dir=rules_path,
        scratch_out=scratch_out,
        verify_step=verify_step,
        stop_step=render_prompt(
            "stop-conditions-ignored.md"
            if ignore_stop_conditions
            else "stop-conditions.md"
        ),
        try_rebuild_intermittent=TRY_REBUILD_INTERMITTENT,
        try_rebuild_permafail=TRY_REBUILD_PERMAFAIL,
        try_ref=TRY_REF,
    )

    result_msg: ResultMessage | None = None
    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(f"bug {bug}")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                reporter.message(msg)
                if isinstance(msg, ResultMessage):
                    result_msg = msg

    if result_msg is None:
        raise AgentError(f"bug {bug}: agent produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"bug {bug} investigation failed: {result_msg.result or result_msg.subtype}"
        )
    blocker = scratch_out / "error.txt"
    if blocker.exists():
        raise AgentError(blocker.read_text().strip() or "agent reported it was blocked")

    return assemble_result(
        bug=bug,
        scratch_out=scratch_out,
        result_msg=result_msg,
        source_repo=source_repo,
        publish_file=publish_file,
    )
