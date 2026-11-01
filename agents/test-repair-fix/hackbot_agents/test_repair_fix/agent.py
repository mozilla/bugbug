# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Test-repair-fix agent: propose a patch for a regression test-repair blamed.

Started by a test-repair run's ``agents.start_run`` action. Works from that run's
analysis, verdict and failure logs, on a checkout of the culprit itself, so the
patch is a change to the culprit for its author to squash in and reland. Its own
documents are written for that author, not for the sheriffs the analysis serves.
"""

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

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
from hackbot_agents.test_repair.agent import TestRepairResult, failing_tests
from hackbot_agents.test_repair.resolve import Investigation
from hackbot_runtime import ActionsRecorder, AgentError, HackbotAgentResult
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.claude_sdk import actions_server_for, actions_to_tool_names
from hackbot_runtime.claude import Reporter

from .config import (
    ADDITIONAL_DIRS,
    ALLOWED_TOOLS,
    BUGZILLA_READ_TOOLS,
    ENABLED_ACTION_TYPES,
    FIREFOX_TOOLS,
    FIX_MODEL,
    SKIP_FIREFOX_BUILD,
)
from .prompts import (
    BUG_NOTE,
    ENVIRONMENT_NOTE,
    FIX_TEMPLATE,
    PARENT_REVISION_ARG,
    REPORT_INSTRUCTIONS,
    SYSTEM_PROMPT_APPEND,
    VERIFY_LOCAL,
    VERIFY_REMOTE,
    VERIFY_SKIPPED,
)


class TestRepairFixResult(HackbotAgentResult):
    culprit_commit: str
    culprit_bug: int | None = None
    proposed_patch: bool = False
    verified: bool = False
    summary: str = ""
    fix: str = ""


_SANITIZER_OPTIONS = {"asan": "address-sanitizer", "tsan": "thread-sanitizer"}
_REVISION_RE = re.compile(
    r"^Differential Revision: https://phabricator\.services\.mozilla\.com/D(\d+)",
    re.MULTILINE,
)


def _build_options(
    *,
    model: str,
    cwd: Path,
    scratch_dir: Path,
    mcp_servers: dict[str, McpServerConfig],
    allowed_tools: list[str],
    max_turns: int | None,
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=model,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": SYSTEM_PROMPT_APPEND,
        },
        cwd=str(cwd),
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        disallowed_tools=["AskUserQuestion", "Task", "Skill"],
        add_dirs=[*ADDITIONAL_DIRS, str(scratch_dir)],
        permission_mode="bypassPermissions",
        effort="high",
        max_turns=max_turns,
        # Loads the Firefox tree's CLAUDE.md, which imports its AGENTS.md.
        setting_sources=["project"],
        # Ignores the tree's .mcp.json; only mcp_servers are available.
        extra_args={"strict-mcp-config": None},
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


def _check(result_msg: ResultMessage | None) -> None:
    if result_msg is None:
        raise AgentError("fix session produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"fix session failed: {result_msg.result or result_msg.subtype}"
        )


def _write_mozconfig(fx_ctx: FirefoxContext, investigation: Investigation) -> None:
    """Write a mozconfig mirroring the failing CI build's variant."""
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
    """Install the build toolchain. Idempotent; a failure is not fatal."""
    result = await bootstrap_firefox(fx_ctx.source_dir)
    if not result.get("success"):
        print(f"[test-repair-fix] bootstrap: {result.get('message')}", file=sys.stderr)


def _commit_message(repo: Path, sha: str) -> str:
    """A commit's full message from the local checkout; empty when unreadable."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%B", sha],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError, OSError:
        return ""


def _revision_from_commit(repo: Path, sha: str) -> int | None:
    """The Phabricator revision a commit landed from, off its footer."""
    match = _REVISION_RE.search(_commit_message(repo, sha))
    return int(match.group(1)) if match else None


def _read_result(out_dir: Path) -> dict:
    try:
        return json.loads((out_dir / "result.json").read_text())
    except ValueError, OSError:
        return {}


def _read_doc(
    out_dir: Path,
    name: str,
    publish_file: Callable[[str, Path, str | None], str] | None,
) -> str:
    path = out_dir / name
    if not path.exists():
        return ""
    if publish_file is not None:
        publish_file(name, path, "text/markdown")
    return path.read_text()


async def run_fix(
    *,
    bugzilla_mcp_server: McpServerConfig | None,
    source_repo: Path,
    fx_ctx: FirefoxContext,
    investigation: Investigation,
    analysis: TestRepairResult,
    analysis_dir: Path,
    out_dir: Path,
    skip_firefox_build: bool = SKIP_FIREFOX_BUILD,
    model: str | None = None,
    max_turns: int | None = None,
    verbose: bool = False,
    log: Path | None = None,
    publish_file: Callable[[str, Path, str | None], str] | None = None,
    actions_recorder: ActionsRecorder | None = None,
) -> TestRepairFixResult:
    """Propose a patch for the culprit ``analysis`` blamed.

    ``source_repo`` is checked out at the culprit, ``analysis_dir`` holds the
    analysis run's documents and failure logs, and the agent writes its own to
    ``out_dir``. Pass ``actions_recorder`` to let the
    agent submit the patch for review as a Phabricator revision, recorded as a
    proposed action. Runs without a recorder, or with no bug for the culprit, only
    produce the patch in the source tree.
    """
    culprit_commit = analysis.culprit_commit
    mcp_servers: dict[str, McpServerConfig] = {}
    allowed_tools = [*ALLOWED_TOOLS]
    if skip_firefox_build:
        verify_step = VERIFY_SKIPPED
    else:
        mcp_servers["firefox"] = build_sdk_server("firefox", fx_ctx, firefox.TOOLS)
        allowed_tools += FIREFOX_TOOLS
        _write_mozconfig(fx_ctx, investigation)
        await _bootstrap(fx_ctx)
        template = VERIFY_LOCAL if investigation.is_linux else VERIFY_REMOTE
        verify_step = template.format(
            harness=investigation.harness, platform=investigation.platform
        ) + ENVIRONMENT_NOTE.format(platform=investigation.platform)
    if bugzilla_mcp_server:
        mcp_servers["bugzilla"] = bugzilla_mcp_server
        allowed_tools += BUGZILLA_READ_TOOLS

    bug_id = analysis.culprit_bug
    parent_revision = _revision_from_commit(source_repo, culprit_commit)
    report = actions_recorder is not None and bug_id is not None
    if report:
        _, actions_server = actions_server_for(
            actions_recorder, types=ENABLED_ACTION_TYPES
        )
        mcp_servers[ACTIONS_SERVER_NAME] = actions_server
        allowed_tools += actions_to_tool_names(ENABLED_ACTION_TYPES)
    elif actions_recorder is not None:
        print(
            f"[test-repair-fix] no bug for culprit {culprit_commit}: the patch "
            "will be produced but not submitted for review, since a "
            "Phabricator revision is filed against a bug",
            file=sys.stderr,
        )

    prompt = FIX_TEMPLATE.format(
        culprit_commit=culprit_commit,
        bug_note=BUG_NOTE.format(bug_id=bug_id) if bug_id else "",
        confidence=analysis.confidence,
        failing_tests=failing_tests(investigation),
        harness=investigation.harness,
        platform=investigation.platform or "unknown",
        label=investigation.label or "unknown",
        project=investigation.project,
        hg_revision=investigation.hg_revision,
        verify_step=verify_step,
        source_repo=source_repo,
        analysis_dir=analysis_dir,
        out_dir=out_dir,
        report=(
            REPORT_INSTRUCTIONS.format(
                bug_id=bug_id,
                parent=(
                    PARENT_REVISION_ARG.format(revision=parent_revision)
                    if parent_revision
                    else ""
                ),
            )
            if report
            else ""
        ),
    )

    label = (
        investigation.failing_groups[0].group
        if investigation.failing_groups
        else investigation.hg_revision[:12]
    )
    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(f"{label}: fix")
        options = _build_options(
            model=model or FIX_MODEL,
            cwd=source_repo,
            scratch_dir=out_dir.parent,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
        )
        result_msg = await _run_session(reporter, options, prompt)
        _check(result_msg)

    outcome = _read_result(out_dir)
    return TestRepairFixResult(
        culprit_commit=culprit_commit,
        culprit_bug=bug_id,
        proposed_patch=bool(outcome.get("proposed_patch")),
        verified=bool(outcome.get("verified")),
        summary=_read_doc(out_dir, "summary.md", publish_file),
        fix=_read_doc(out_dir, "fix.md", publish_file),
        num_turns=result_msg.num_turns or 0,
        total_cost_usd=result_msg.total_cost_usd or 0.0,
    )
