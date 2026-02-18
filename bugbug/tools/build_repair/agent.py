# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import subprocess
from logging import getLogger
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from pydantic import BaseModel, Field

from bugbug.tools.base import GenerativeModelTool
from bugbug.tools.build_repair.config import (
    ANALYSIS_MODEL,
    CLAUDE_PERMISSIONS_CONFIG,
    DEFAULT_MAX_TURNS,
    FIREFOX_MCP_URL,
    FIX_MODEL,
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

    Stage 1 (Opus): Analyzes the failure and produces analysis/planning/summary docs.
    Stage 2 (Sonnet): Reads the analysis and implements a fix. Skipped in analysis-only mode.
    After Stage 2, commits the fix, runs ./mach build, and optionally submits to try.
    """

    def __init__(
        self,
        target_software: str = "Mozilla Firefox",
        analysis_only: bool = False,
        analysis_model: str = ANALYSIS_MODEL,
        fix_model: str = FIX_MODEL,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        self.target_software = target_software
        self.analysis_only = analysis_only
        self.analysis_model = analysis_model
        self.fix_model = fix_model
        self.max_turns = max_turns

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

    def _write_settings(self, worktree_path: Path) -> None:
        settings_dir = worktree_path / ".claude"
        settings_dir.mkdir(exist_ok=True)
        (settings_dir / "settings.json").write_text(
            json.dumps(CLAUDE_PERMISSIONS_CONFIG, indent=2)
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
        self._prepare_input_files(failure, worktree_path)
        self._write_settings(worktree_path)

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            target_software=self.target_software
        )
        mcp_servers = [{"url": FIREFOX_MCP_URL, "name": "firefox"}]
        disallowed = ["AskUserQuestion", "Task"]
        total_cost = 0.0
        total_turns = 0

        # Stage 1: Analysis
        stage1_options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=self.analysis_model,
            cwd=str(worktree_path),
            disallowed_tools=disallowed,
            permission_mode="default",
            setting_sources=["project"],
            max_turns=self.max_turns,
            max_thinking_tokens=16000,
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
            return AgentResponse(
                error=str(e),
                cost_usd=total_cost,
                num_turns=total_turns,
            )

        summary = self._read_output(failure, worktree_path, "summary")
        analysis = self._read_output(failure, worktree_path, "analysis")

        if self.analysis_only:
            return AgentResponse(
                summary=summary,
                analysis=analysis,
                cost_usd=total_cost,
                num_turns=total_turns,
            )

        # Stage 2: Fix
        stage2_options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=self.fix_model,
            cwd=str(worktree_path),
            disallowed_tools=disallowed,
            permission_mode="default",
            setting_sources=["project"],
            max_turns=self.max_turns,
            mcp_servers=mcp_servers,
        )
        fix_prompt = FIX_TEMPLATE.format(bug_id=failure.bug_id)
        try:
            async for message in query(prompt=fix_prompt, options=stage2_options):
                if isinstance(message, ResultMessage):
                    total_cost += message.total_cost_usd or 0
                    total_turns += message.num_turns or 0
        except Exception as e:
            return AgentResponse(
                summary=summary,
                analysis=analysis,
                error=str(e),
                cost_usd=total_cost,
                num_turns=total_turns,
            )

        diff_result = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        diff = diff_result.stdout

        if not diff.strip():
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
        try_result = run_try_verification(
            worktree_path=worktree_path,
            bug_id=failure.bug_id,
            task_name=task_name,
            skip_try_push=skip_try_push,
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
