# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
import traceback
from collections.abc import Callable
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
    EVAL_PROMPT,
    FIX_TEMPLATE,
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
    error_traceback: str | None = Field(default=None)
    failure_stage: str | None = Field(default=None)
    cost_usd: float = Field(default=0.0)
    num_turns: int = Field(default=0)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cache_read_input_tokens: int = Field(default=0)
    cache_creation_input_tokens: int = Field(default=0)
    local_build_passed: bool | None = Field(default=None)
    try_build_passed: bool | None = Field(default=None)
    lando_job_id: str | None = Field(default=None)
    treeherder_url: str | None = Field(default=None)
    stage1_transcript: list[dict] = Field(default_factory=list)
    stage2_transcript: list[dict] = Field(default_factory=list)


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
        eval_mode: bool = False,
        analysis_model: str = ANALYSIS_MODEL,
        fix_model: str = FIX_MODEL,
    ) -> None:
        self.eval_mode = eval_mode
        self.target_software = target_software
        self.analysis_only = analysis_only
        self.analysis_model = analysis_model
        self.fix_model = fix_model

    @classmethod
    def create(cls, **kwargs):
        return cls(**kwargs)

    @staticmethod
    def _usage_fields(usage: dict) -> dict:
        return {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        }

    @staticmethod
    def _serialize_message(message) -> dict:
        data = {"type": type(message).__name__}
        if hasattr(message, "model_dump"):
            data.update(message.model_dump())
        elif hasattr(message, "__dict__"):
            data.update(vars(message))
        else:
            data["raw"] = str(message)
        return data

    async def _run_stage(
        self,
        stage_name: str,
        prompt: str,
        model: str,
        options: ClaudeAgentOptions,
        bug_id: int,
        on_message: Callable[[str, dict], None] | None = None,
    ) -> tuple[list[dict], float, int, dict]:
        transcript: list[dict] = []
        cost = 0.0
        turns = 0
        result_data: dict = {}
        usage: dict = {}

        if on_message:
            on_message(
                stage_name,
                {
                    "type": "stage_start",
                    "prompt": prompt,
                    "model": model,
                },
            )
        try:
            async for message in query(prompt=prompt, options=options):
                serialized = self._serialize_message(message)
                transcript.append(serialized)
                logger.info(f"Bug {bug_id}: {stage_name} [{serialized['type']}]")
                logger.debug(f"Bug {bug_id}: {stage_name} detail: {serialized}")
                if on_message:
                    on_message(stage_name, serialized)
                if isinstance(message, ResultMessage):
                    cost += message.total_cost_usd or 0
                    turns += message.num_turns or 0
                    usage = getattr(message, "usage", {}) or {}
                    result_data = serialized
        finally:
            if on_message:
                on_message(
                    stage_name,
                    {
                        "type": "stage_end",
                        "cost_usd": cost,
                        "num_turns": turns,
                        "result_data": result_data,
                    },
                )

        return transcript, cost, turns, usage

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
            f"Prepared input files for bug {failure.bug_id} at {in_dir} "
            f"({len(failure.failure_tasks)} failure tasks)"
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
        on_message: Callable[[str, dict], None] | None = None,
    ) -> AgentResponse:
        logger.info(
            f"Starting build repair for bug {failure.bug_id} "
            f"(commit={failure.git_commit}, worktree={worktree_path}, "
            f"analysis_only={self.analysis_only}, skip_try_push={skip_try_push})"
        )
        self._prepare_input_files(failure, worktree_path)

        mcp_servers = {"firefox": {"type": "http", "url": FIREFOX_MCP_URL}}
        disallowed = ["AskUserQuestion", "Task"]
        total_cost = 0.0
        total_turns = 0
        total_usage: dict = {}

        logger.info(
            f"Bug {failure.bug_id}: starting Stage 1 (analysis) "
            f"with model={self.analysis_model}"
        )
        stage1_options = ClaudeAgentOptions(
            model=self.analysis_model,
            cwd=str(worktree_path),
            allowed_tools=ALLOWED_TOOLS,
            disallowed_tools=disallowed,
            add_dirs=ADDITIONAL_DIRS,
            sandbox=SANDBOX_CONFIG,
            permission_mode="acceptEdits",
            effort="high",
            mcp_servers=mcp_servers,
        )
        analysis_prompt = ANALYSIS_TEMPLATE.format(
            bug_id=failure.bug_id,
            target_software=self.target_software,
            eval=EVAL_PROMPT if self.eval_mode else "",
        )
        try:
            (
                stage1_transcript,
                stage1_cost,
                stage1_turns,
                stage1_usage,
            ) = await self._run_stage(
                "analysis",
                analysis_prompt,
                self.analysis_model,
                stage1_options,
                failure.bug_id,
                on_message,
            )
            total_cost += stage1_cost
            total_turns += stage1_turns
            for k, v in stage1_usage.items():
                if isinstance(v, (int, float)):
                    total_usage[k] = total_usage.get(k, 0) + v
        except Exception as e:
            logger.error(
                f"Bug {failure.bug_id}: Stage 1 (analysis) failed: {e}", exc_info=True
            )
            return AgentResponse(
                error=str(e),
                error_traceback=traceback.format_exc(),
                failure_stage="analysis",
                cost_usd=total_cost,
                num_turns=total_turns,
                **self._usage_fields(total_usage),
            )

        logger.info(
            f"Bug {failure.bug_id}: Stage 1 complete "
            f"(cost=${total_cost:.4f}, turns={total_turns})"
        )

        summary = self._read_output(failure, worktree_path, "summary")
        analysis = self._read_output(failure, worktree_path, "analysis")
        logger.info(
            f"Bug {failure.bug_id}: read output files "
            f"(summary={len(summary)} chars, analysis={len(analysis)} chars)"
        )

        if self.analysis_only:
            logger.info(f"Bug {failure.bug_id}: analysis-only mode, skipping Stage 2")
            return AgentResponse(
                summary=summary,
                analysis=analysis,
                cost_usd=total_cost,
                num_turns=total_turns,
                **self._usage_fields(total_usage),
                stage1_transcript=stage1_transcript,
            )

        logger.info(
            f"Bug {failure.bug_id}: starting Stage 2 (fix) with model={self.fix_model}"
        )
        stage2_options = ClaudeAgentOptions(
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
        fix_prompt = FIX_TEMPLATE.format(
            bug_id=failure.bug_id, eval=EVAL_PROMPT if self.eval_mode else ""
        )
        try:
            (
                stage2_transcript,
                stage2_cost,
                stage2_turns,
                stage2_usage,
            ) = await self._run_stage(
                "fix",
                fix_prompt,
                self.fix_model,
                stage2_options,
                failure.bug_id,
                on_message,
            )
            total_cost += stage2_cost
            total_turns += stage2_turns
            for k, v in stage2_usage.items():
                if isinstance(v, (int, float)):
                    total_usage[k] = total_usage.get(k, 0) + v
        except Exception as e:
            logger.error(
                f"Bug {failure.bug_id}: Stage 2 (fix) failed: {e}", exc_info=True
            )
            return AgentResponse(
                summary=summary,
                analysis=analysis,
                error=str(e),
                error_traceback=traceback.format_exc(),
                failure_stage="fix",
                cost_usd=total_cost,
                num_turns=total_turns,
                **self._usage_fields(total_usage),
            )

        logger.info(
            f"Bug {failure.bug_id}: Stage 2 complete "
            f"(cost=${total_cost:.4f}, turns={total_turns})"
        )

        subprocess.run(
            ["git", "add", "-A"],
            cwd=worktree_path,
            capture_output=True,
        )
        diff_result = subprocess.run(
            ["git", "diff", "--staged", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        diff = diff_result.stdout
        logger.info(f"Bug {failure.bug_id}: git diff produced {len(diff)} chars")

        if not diff.strip():
            logger.warning(f"Bug {failure.bug_id}: no diff produced, returning early")
            return AgentResponse(
                summary=summary,
                analysis=analysis,
                diff=diff,
                cost_usd=total_cost,
                num_turns=total_turns,
                **self._usage_fields(total_usage),
                stage1_transcript=stage1_transcript,
                stage2_transcript=stage2_transcript,
            )

        from bugbug.tools.build_repair.try_server import run_try_verification

        task_name = (
            failure.failure_tasks[0]["task_name"] if failure.failure_tasks else ""
        )
        logger.info(
            f"Bug {failure.bug_id}: starting try verification "
            f"(task={task_name}, skip_try_push={skip_try_push})"
        )
        try_result = run_try_verification(
            worktree_path=worktree_path,
            bug_id=failure.bug_id,
            task_name=task_name,
            skip_try_push=skip_try_push,
        )

        logger.info(
            f"Bug {failure.bug_id}: try verification done "
            f"(local_build={try_result.local_build_passed}, "
            f"try_build={try_result.try_build_passed}, "
            f"lando_job={try_result.lando_job_id}, "
            f"total_cost=${total_cost:.4f}, total_turns={total_turns})"
        )
        return AgentResponse(
            summary=summary,
            analysis=analysis,
            diff=diff,
            cost_usd=total_cost,
            num_turns=total_turns,
            **self._usage_fields(total_usage),
            local_build_passed=try_result.local_build_passed,
            try_build_passed=try_result.try_build_passed,
            lando_job_id=try_result.lando_job_id,
            treeherder_url=try_result.treeherder_url,
            stage1_transcript=stage1_transcript,
            stage2_transcript=stage2_transcript,
        )
