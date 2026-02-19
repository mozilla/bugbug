# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
from logging import getLogger
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from pydantic import BaseModel, Field

from bugbug.tools.base import GenerativeModelTool
from bugbug.tools.build_repair.config import (
    ADDITIONAL_DIRS,
    ALLOWED_TOOLS,
    ANALYSIS_MODEL,
    FIREFOX_MCP_URL,
    FIX_MODEL,
    SANDBOX_CONFIG,
)
from bugbug.tools.build_repair.prompts import (
    ANALYSIS_TEMPLATE,
    FIX_TEMPLATE,
    SYSTEM_PROMPT_TEMPLATE,
)

logger = getLogger(__name__)


class BuildFailure(BaseModel):
    """Input describing a build failure from the dataset."""

    bug_id: int = Field(description="The ID of the bug in Bugzilla.")
    bug_title: str | None = Field(default=None, description="Optional bug title.")
    bug_comments: list[str] | None = Field(
        default=None, description="Optional bug comments."
    )
    git_commit: str = Field(description="Git revision to checkout.")
    failure_tasks: list[dict] = Field(
        description="List of {task_name, task_id, retry_id, failure_lines}."
    )


class AgentResponse(BaseModel):
    """Output from a build repair run, including analysis, diff, cost, and build results."""

    summary: str = Field(default="")
    analysis: str = Field(default="")
    diff: str = Field(default="")
    error: str | None = Field(default=None)
    cost_usd: float = Field(default=0.0)
    num_turns: int = Field(default=0)
    local_build_passed: bool | None = Field(default=None)
    try_build_passed: bool | None = Field(default=None)
    lando_job_id: str | None = Field(default=None)
    treeherder_url: str | None = Field(default=None)


class BuildRepairTool(GenerativeModelTool):
    """Two-stage build repair agent using Claude Agent SDK.

    Stage 1: Analyzes the failure and produces analysis/planning/summary docs.
    Stage 2: Reads the analysis and implements a fix. Skipped in analysis-only mode.
    After Stage 2, commits the fix, runs ./mach build, and optionally submits to try.
    """

    def __init__(
        self,
        target_software: str = "Mozilla Firefox",
        analysis_only: bool = False,
        analysis_model: str = ANALYSIS_MODEL,
        fix_model: str = FIX_MODEL,
    ) -> None:
        self.target_software = target_software
        self.analysis_only = analysis_only
        self.analysis_model = analysis_model
        self.fix_model = fix_model

    @classmethod
    def create(cls, **kwargs):
        return cls(**kwargs)

    def _prepare_input_files(self, failure: BuildFailure, worktree_path: Path) -> None:
        in_dir = worktree_path / "repair_agent" / "in" / str(failure.bug_id)
        in_dir.mkdir(parents=True, exist_ok=True)

        (in_dir / "bug_description.md").write_text(
            f"# Bug {failure.bug_id}: {failure.bug_title}\n\n"
            + "\n\n---\n\n".join(failure.bug_comments or [])
        )

        logs_content = ""
        for task in failure.failure_tasks:
            logs_content += f"## {task['task_name']} (task_id: {task['task_id']})\n\n"
            logs_content += "\n".join(task["failure_lines"]) + "\n\n"
        (in_dir / "build_failure_logs.md").write_text(logs_content)

        out_dir = worktree_path / "repair_agent" / "out" / str(failure.bug_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Prepared input files for bug %d at %s (%d failure tasks)",
            failure.bug_id,
            in_dir,
            len(failure.failure_tasks),
        )

    def _read_output(self, failure: BuildFailure, worktree_path: Path, key: str) -> str:
        path = (
            worktree_path / "repair_agent" / "out" / str(failure.bug_id) / f"{key}.md"
        )
        if path.exists():
            return path.read_text()
        return ""

    async def run(
        self,
        failure: BuildFailure,
        worktree_path: Path,
        skip_try_push: bool = False,
    ) -> AgentResponse:
        logger.info(
            "Starting build repair for bug %d (commit=%s, worktree=%s, "
            "analysis_only=%s, skip_try_push=%s)",
            failure.bug_id,
            failure.git_commit,
            worktree_path,
            self.analysis_only,
            skip_try_push,
        )
        self._prepare_input_files(failure, worktree_path)

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            target_software=self.target_software
        )
        mcp_servers = {"firefox": {"type": "http", "url": FIREFOX_MCP_URL}}
        disallowed = ["AskUserQuestion", "Task"]
        total_cost = 0.0
        total_turns = 0

        logger.info(
            "Bug %d: starting Stage 1 (analysis) with model=%s",
            failure.bug_id,
            self.analysis_model,
        )
        # Stage 1: Analysis
        stage1_options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=self.analysis_model,
            cwd=str(worktree_path),
            allowed_tools=ALLOWED_TOOLS,
            disallowed_tools=disallowed,
            add_dirs=ADDITIONAL_DIRS,
            sandbox=SANDBOX_CONFIG,
            permission_mode="plan",
            effort="high",
            mcp_servers=mcp_servers,
        )
        analysis_prompt = ANALYSIS_TEMPLATE.format(
            bug_id=failure.bug_id,
            target_software=self.target_software,
        )
        try:
            async for message in query(prompt=analysis_prompt, options=stage1_options):
                if isinstance(message, ResultMessage):
                    total_cost += message.total_cost_usd or 0
                    total_turns += message.num_turns or 0
        except Exception as e:
            logger.error(
                "Bug %d: Stage 1 (analysis) failed: %s",
                failure.bug_id,
                e,
                exc_info=True,
            )
            return AgentResponse(
                error=str(e),
                cost_usd=total_cost,
                num_turns=total_turns,
            )

        logger.info(
            "Bug %d: Stage 1 complete (cost=$%.4f, turns=%d)",
            failure.bug_id,
            total_cost,
            total_turns,
        )

        summary = self._read_output(failure, worktree_path, "summary")
        analysis = self._read_output(failure, worktree_path, "analysis")
        logger.info(
            "Bug %d: read output files (summary=%d chars, analysis=%d chars)",
            failure.bug_id,
            len(summary),
            len(analysis),
        )

        if self.analysis_only:
            logger.info("Bug %d: analysis-only mode, skipping Stage 2", failure.bug_id)
            return AgentResponse(
                summary=summary,
                analysis=analysis,
                cost_usd=total_cost,
                num_turns=total_turns,
            )

        logger.info(
            "Bug %d: starting Stage 2 (fix) with model=%s",
            failure.bug_id,
            self.fix_model,
        )
        # Stage 2: Fix
        stage2_options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=self.fix_model,
            cwd=str(worktree_path),
            allowed_tools=ALLOWED_TOOLS,
            disallowed_tools=disallowed,
            add_dirs=ADDITIONAL_DIRS,
            sandbox=SANDBOX_CONFIG,
            permission_mode="acceptEdits",
            effort="low",
            mcp_servers=mcp_servers,
        )
        fix_prompt = FIX_TEMPLATE.format(bug_id=failure.bug_id)
        try:
            async for message in query(prompt=fix_prompt, options=stage2_options):
                if isinstance(message, ResultMessage):
                    total_cost += message.total_cost_usd or 0
                    total_turns += message.num_turns or 0
        except Exception as e:
            logger.error(
                "Bug %d: Stage 2 (fix) failed: %s", failure.bug_id, e, exc_info=True
            )
            return AgentResponse(
                summary=summary,
                analysis=analysis,
                error=str(e),
                cost_usd=total_cost,
                num_turns=total_turns,
            )

        logger.info(
            "Bug %d: Stage 2 complete (cost=$%.4f, turns=%d)",
            failure.bug_id,
            total_cost,
            total_turns,
        )

        diff_result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        diff = diff_result.stdout
        logger.info("Bug %d: git diff produced %d chars", failure.bug_id, len(diff))

        if not diff.strip():
            logger.warning("Bug %d: no diff produced, returning early", failure.bug_id)
            return AgentResponse(
                summary=summary,
                analysis=analysis,
                diff=diff,
                cost_usd=total_cost,
                num_turns=total_turns,
            )

        from bugbug.tools.build_repair.try_server import run_try_verification

        task_name = (
            failure.failure_tasks[0]["task_name"] if failure.failure_tasks else ""
        )
        logger.info(
            "Bug %d: starting try verification (task=%s, skip_try_push=%s)",
            failure.bug_id,
            task_name,
            skip_try_push,
        )
        try_result = run_try_verification(
            worktree_path=worktree_path,
            bug_id=failure.bug_id,
            task_name=task_name,
            skip_try_push=skip_try_push,
        )

        logger.info(
            "Bug %d: try verification done (local_build=%s, try_build=%s, "
            "lando_job=%s, total_cost=$%.4f, total_turns=%d)",
            failure.bug_id,
            try_result.local_build_passed,
            try_result.try_build_passed,
            try_result.lando_job_id,
            total_cost,
            total_turns,
        )
        return AgentResponse(
            summary=summary,
            analysis=analysis,
            diff=diff,
            cost_usd=total_cost,
            num_turns=total_turns,
            local_build_passed=try_result.local_build_passed,
            try_build_passed=try_result.try_build_passed,
            lando_job_id=try_result.lando_job_id,
            treeherder_url=try_result.treeherder_url,
        )
