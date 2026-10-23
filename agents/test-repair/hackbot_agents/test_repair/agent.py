# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Test-repair agent for Firefox CI test failures.

Blame the commit that regressed a failing test, or identify it as a known
intermittent. Read-only: when the culprit is worth a patch, the model hands the
run on to the test-repair-fix agent, which reads the documents published here.
"""

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import ActionsRecorder, AgentError, HackbotAgentResult
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.agents import ACTION_TYPE as START_RUN_ACTION_TYPE
from hackbot_runtime.actions.claude_sdk import actions_server_for, actions_to_tool_names
from hackbot_runtime.claude import Reporter

from .config import (
    ALLOWED_TOOLS,
    ANALYSIS_MODEL,
    BUGZILLA_READ_TOOLS,
    FIX_AGENT,
)
from .prompts import (
    ANALYSIS_TEMPLATE,
    CANDIDATE_INTRO,
    FIX_HANDOFF_STEP,
    KNOWN_INTERMITTENTS_LINE,
    MAX_CANDIDATE_COMMITS,
    MAX_TESTS_PER_GROUP,
    SYSTEM_PROMPT_APPEND,
)
from .resolve import CommitRange, Investigation

_CLASSIFICATIONS = ("regression", "intermittent")
_RECOMMENDATIONS = ("backout", "do_not_backout", "rerun")
_BUG_RE = re.compile(r"^Bug (\d+)", re.IGNORECASE)


class TestRepairResult(HackbotAgentResult):
    classification: Literal["regression", "intermittent"]
    recommendation: Literal["backout", "do_not_backout", "rerun"]
    culprit_commit: str | None = None
    candidate_commits: list[str] = []
    culprit_bug: int | None = None
    confidence: float = 0.0
    intermittent_bug: int | None = None
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
        add_dirs=[str(scratch_dir)],
        permission_mode="bypassPermissions",
        effort=effort,
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


def _check_blocked(scratch_out: Path) -> None:
    """Fail the run unless the agent actually retrieved a failure log.

    Analysing a failure whose log never arrived produces a confident verdict
    invented from the diff alone (bug 6665), so a missing log is an error rather
    than a verdict, and the error reaches the API and the UI. The agent is asked to
    report the blocker itself in error.txt; the log check is what makes it a
    guarantee rather than an instruction.
    """
    blocker = scratch_out / "error.txt"
    if blocker.exists():
        raise AgentError(blocker.read_text().strip() or "agent reported it was blocked")
    logs = scratch_out / "logs"
    if not any(logs.glob("**/*live_backing_log*")):
        raise AgentError(
            "no failure log was retrieved: treeherder-cli left nothing under "
            f"{logs}, so any verdict would be guesswork"
        )


def _check(result_msg: ResultMessage | None, stage: str) -> None:
    if result_msg is None:
        raise AgentError(f"{stage} stage produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"{stage} stage failed: {result_msg.result or result_msg.subtype}"
        )


def read_doc(
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


def read_verdict(scratch_out: Path) -> dict:
    path = scratch_out / "verdict.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError, OSError:
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
    """Normalize a model-authored sha to a full commit hash, or None if unresolvable."""
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
    """Resolve the ranked fallback candidates, preserving order and excluding the culprit."""
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


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return default


def _as_int(value) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _bug_from_commit(repo: Path, sha: str | None) -> int | None:
    """The bug a commit landed for, read off its subject."""
    if not sha:
        return None
    try:
        subject = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%s", sha],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError, OSError:
        return None
    match = _BUG_RE.match(subject.strip())
    return int(match.group(1)) if match else None


def _assemble_result(
    scratch_out: Path,
    *,
    verdict: dict,
    source_repo: Path,
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
        culprit_bug=_bug_from_commit(source_repo, culprit_commit),
        intermittent_bug=_as_int(verdict.get("intermittent_bug")),
        confidence=_as_float(verdict.get("confidence")),
        summary=read_doc(scratch_out, "summary", publish_file),
        analysis=read_doc(scratch_out, "analysis", publish_file),
        num_turns=total_turns,
        total_cost_usd=total_cost,
    )


def _range_expr(commit_range: CommitRange) -> str:
    """The range for `git log`, relative to the checked-out head."""
    return f"HEAD~{commit_range.span}..HEAD"


def failing_tests(investigation: Investigation) -> str:
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


def _publish_handoff(
    scratch_out: Path, publish_file: Callable[[str, Path, str | None], str]
) -> None:
    """Publish the verdict and the failure logs the fix agent works from."""
    publish_file("verdict.json", scratch_out / "verdict.json", "application/json")
    for path in sorted((scratch_out / "logs").glob("**/*live_backing_log*")):
        publish_file(str(path.relative_to(scratch_out)), path, "text/plain")


def _fix_requests(recorder: ActionsRecorder) -> list[dict]:
    return [
        action
        for action in recorder.list_actions()
        if action["type"] == START_RUN_ACTION_TYPE
    ]


async def run_test_repair(
    *,
    bugzilla_mcp_server: McpServerConfig | None,
    source_repo: Path,
    investigation: Investigation,
    scratch_out: Path,
    model: str | None = None,
    max_turns: int | None = None,
    verbose: bool = False,
    log: Path | None = None,
    publish_file: Callable[[str, Path, str | None], str] | None = None,
    actions_recorder: ActionsRecorder | None = None,
) -> TestRepairResult:
    """Blame the commit that regressed a failing test.

    Pass ``actions_recorder`` to let the model hand a blamed culprit on to the
    test-repair-fix agent, recorded as an ``agents.start_run`` action that starts
    it once this run has succeeded. A hand-off without a culprit is dropped.
    """
    commit_range = investigation.commit_range
    failure_commit = investigation.failure_commit
    print(
        f"[test-repair] analyzing {investigation.hg_revision} at {failure_commit}",
        file=sys.stderr,
    )

    mcp_servers: dict[str, McpServerConfig] = {}
    allowed_tools = [*ALLOWED_TOOLS]
    if bugzilla_mcp_server:
        mcp_servers["bugzilla"] = bugzilla_mcp_server
        allowed_tools += BUGZILLA_READ_TOOLS
    if actions_recorder is not None:
        _, actions_server = actions_server_for(
            actions_recorder, types=[START_RUN_ACTION_TYPE]
        )
        mcp_servers[ACTIONS_SERVER_NAME] = actions_server
        allowed_tools += actions_to_tool_names([START_RUN_ACTION_TYPE])

    known_intermittents_line = (
        KNOWN_INTERMITTENTS_LINE.format(
            bugs=", ".join(str(bug) for bug in investigation.known_intermittent_bugs)
        )
        if investigation.known_intermittent_bugs
        else ""
    )
    range_expr = _range_expr(commit_range)
    analysis_prompt = ANALYSIS_TEMPLATE.format(
        failing_tests=failing_tests(investigation),
        harness=investigation.harness,
        platform=investigation.platform or "unknown",
        label=investigation.label or "unknown",
        project=investigation.project,
        hg_revision=investigation.hg_revision,
        source_repo=source_repo,
        failure_commit=failure_commit,
        candidate_intro=CANDIDATE_INTRO.format(
            commit_range=range_expr, span=commit_range.span
        ),
        commit_range=range_expr,
        max_candidates=MAX_CANDIDATE_COMMITS,
        known_intermittents_line=known_intermittents_line,
        scratch_out=scratch_out,
        fix_handoff=(
            FIX_HANDOFF_STEP.format(agent=FIX_AGENT)
            if actions_recorder is not None
            else ""
        ),
    )

    label = (
        investigation.failing_groups[0].group
        if investigation.failing_groups
        else investigation.hg_revision[:12]
    )
    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(f"{label}: analysis")
        options = _build_options(
            model=model or ANALYSIS_MODEL,
            effort="high",
            cwd=source_repo,
            scratch_dir=scratch_out.parent,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
        )
        result_msg = await _run_session(reporter, options, analysis_prompt)
        _check(result_msg, "analysis")
        _check_blocked(scratch_out)

    result = _assemble_result(
        scratch_out,
        verdict=read_verdict(scratch_out),
        source_repo=source_repo,
        total_turns=result_msg.num_turns or 0,
        total_cost=result_msg.total_cost_usd or 0.0,
        publish_file=publish_file,
    )

    fix_requests = _fix_requests(actions_recorder) if actions_recorder else []
    if fix_requests and not result.culprit_commit:
        print("[test-repair] no culprit to fix; not handing off", file=sys.stderr)
        for action in fix_requests:
            actions_recorder.remove_action(action["action_id"])
        fix_requests = []
    if fix_requests and publish_file is not None:
        _publish_handoff(scratch_out, publish_file)
    return result
