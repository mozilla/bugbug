"""Firefox web-compatibility intervention agent.

Plans an intervention for a broken site based on existing reproduction script,
then writes the intervention and verifies it against the same script. The bug is
passed either inline as ``bug_data`` text or a Bugzilla ``bug_id`` (read via
Bugzilla broker).

Given a ``revision_id`` and ``comment``, it instead follows up on a revision it
filed earlier: it skips planning and reproduction, addresses the review
comments, and updates the revision or replies on it.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
from agent_tools.registry import ToolError
from autowebcompat_tools.environment import Environment
from autowebcompat_tools.inputs import AutoWebcompatInput, BugDataInput, BugIdInput
from autowebcompat_tools.task import RunTracker, StructuredTask, TaskConfig
from claude_agent_sdk import ClaudeAgentOptions, McpServerConfig
from claude_agent_sdk.types import SystemPromptPreset
from hackbot_runtime import ActionsRecorder
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.claude_sdk import actions_server_for, actions_to_tool_names
from hackbot_runtime.actions.phabricator import PATCH_ACTION_TYPES
from pydantic import BaseModel

from .browser import FirefoxBrowsers
from .config import (
    BUGZILLA_READ_TOOLS,
    DEVTOOLS_TOOLS,
    FIREFOX_BUILD_TOOLS,
    FOLLOW_UP_ACTIONS,
    INTERVENTION_ACTIONS,
)
from .mcp_servers import build_firefox_devtools_server
from .result import (
    InterventionPlanResult,
    InterventionResult,
    ReproScriptResult,
)

logger = logging.getLogger("autowebcompat-intervention")

HERE = Path(__file__).resolve().parent

WORK_DIR = Path("/app/autowebcompat-intervention")
NODE_MODULES = WORK_DIR / "node_modules"


class PromptLibrary:
    def __init__(self, base: Path = HERE / "prompts"):
        self.base = base

    @property
    def system(self) -> SystemPromptPreset:
        return {
            "type": "preset",
            "preset": "claude_code",
            "append": (self.base / "rules.md").read_text(),
        }

    def get(self, name: str, **fields: object) -> str:
        return (self.base / name).read_text().format(**fields)


PROMPTS = PromptLibrary()


def render_report(input_data: AutoWebcompatInput, fetch_instructions: str) -> str:
    """Hand over the report inline, or point at the Bugzilla bug to fetch."""
    if isinstance(input_data, BugDataInput):
        return (
            f"Here is the web-compatibility report to work on:\n\n{input_data.bug_data}"
        )
    return (
        "The web-compatibility report to work on is Bugzilla bug "
        f"{input_data.bug_id}. {fetch_instructions}"
    )


class AutowebcompatInterventionResult(BaseModel):
    wrote_intervention: bool
    fixed_with_intervention: bool
    build_succeeded: bool
    failure_reason: str | None
    summary: str
    plan_result: InterventionPlanResult | None
    files_changed: list[str] = []


def write_mozconfig(fx_ctx: FirefoxContext) -> None:
    """Write an artifact-build mozconfig targeting the configured objdir."""
    fx_ctx.mozconfig.write_text(
        "\n".join(
            [
                "ac_add_options --enable-application=browser",
                "ac_add_options --enable-artifact-builds",
                "ac_add_options --disable-debug",
                f"mk_add_options MOZ_OBJDIR={fx_ctx.objdir}",
            ]
        )
        + "\n"
    )


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def changed_files(repo: Path, base: str) -> list[str]:
    """Repo-relative paths changed since ``base``, committed or not."""
    tracked = git(repo, "diff", "--name-only", base).splitlines()
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return sorted(set(tracked + untracked))


# An intervention patch may only touch the webcompat add-on and its tests;
# anything else is rejected at submit_patch and drops the patch at the end.
ALLOWED_CHANGE_PATHS = (
    "browser/extensions/webcompat/",
    "testing/webcompat/",
)


def unexpected_changes(changed: list[str]) -> list[str]:
    return [path for path in changed if not path.startswith(ALLOWED_CHANGE_PATHS)]


def script_command(
    script_path: Path, firefox_path: Path, headless: bool
) -> tuple[list[str], dict[str, str]]:
    env = {
        "NODE_PATH": str(NODE_MODULES),
        "BROWSER": "firefox",
        "BROWSER_BIN": str(firefox_path),
    }
    if headless:
        env["HEADLESS"] = "1"
    return ["node", str(script_path)], env


def as_shell(cmd: list[str], env: dict[str, str], cwd: Path) -> str:
    assignments = " ".join(f"{key}={shlex.quote(value)}" for key, value in env.items())
    return f"cd {shlex.quote(str(cwd))} && {assignments} {shlex.join(cmd)}"


def run_script(script_path: Path, firefox_path: Path, headless: bool) -> int | None:
    """Run the reproduction script against one Firefox; return its exit code.

    Returns ``None`` when the script gave no verdict -- it timed out, or it
    exited without printing the verdict line.
    Otherwise, follows the repro-script contract used across the autowebcompat
    agents: 0 = the functionality worked, 1 = the breakage reproduced.
    """
    cmd, env = script_command(script_path, firefox_path, headless)
    script_timeout = 5 * 60

    try:
        proc = subprocess.run(
            cmd,
            cwd=script_path.parent,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=script_timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("repro script timed out after %ss", script_timeout)
        return None

    logger.info(
        "repro script exited %s\nstdout:\n%s\nstderr:\n%s",
        proc.returncode,
        proc.stdout,
        proc.stderr,
    )

    if "worked in" not in proc.stdout:
        logger.warning(
            "repro script exited %s without reaching a verdict; treating as no "
            "verdict rather than as a reproduction",
            proc.returncode,
        )
        return None

    return proc.returncode


def run_confirmation_script(
    script_path: Path, firefox_path: Path, headless: bool
) -> ReproScriptResult:
    exit_code = run_script(script_path, firefox_path, headless)
    if exit_code == 1:
        return ReproScriptResult(
            reproduced=True, summary="The reproduction script confirmed the breakage."
        )
    if exit_code == 0:
        return ReproScriptResult(
            reproduced=False,
            summary="The reproduction script reports the site works (exit 0).",
        )
    return ReproScriptResult(
        reproduced=False,
        summary=(
            f"The reproduction script reached no verdict (exit {exit_code}); it "
            f"timed out or failed before probing the site."
        ),
    )


class InterventionPlan(StructuredTask[InterventionPlanResult]):
    """Read the bug, download its repro script, and propose an intervention type."""

    name = "intervention_plan"
    result_cls = InterventionPlanResult
    work_dir = WORK_DIR

    def system_prompt(self) -> SystemPromptPreset:
        return PROMPTS.system

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        input_data: AutoWebcompatInput,
        bugzilla_mcp_server: McpServerConfig,
    ):
        super().__init__(task_config, run_tracker)
        self.input_data = input_data
        self.script_path = self.work_dir / "reproduction.mjs"
        if self.input_data.type == "bug_id":
            self.add_mcp_server("bugzilla", bugzilla_mcp_server, BUGZILLA_READ_TOOLS)

    def subject(self) -> Any:
        return self.input_data.subject()

    def user_prompt(self) -> str:
        return PROMPTS.get(
            "plan.md",
            report=render_report(
                self.input_data,
                "Fetch it using the Bugzilla MCP tools and request `cf_user_story` "
                "explicitly: it is not in the default field set.",
            ),
            script_path=self.script_path,
        )


class Intervention(StructuredTask[InterventionResult]):
    name = "intervention"
    result_cls = InterventionResult
    work_dir = WORK_DIR

    def system_prompt(self) -> SystemPromptPreset:
        return PROMPTS.system

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        input_data: AutoWebcompatInput,
        plan_result: InterventionPlanResult | None,
        source_repo: Path,
        fx_ctx: FirefoxContext,
        bugzilla_mcp_server: McpServerConfig,
        phabricator_mcp_server: McpServerConfig,
        phabricator_read_tools: list[str],
        actions_server: McpServerConfig,
        action_tools: list[str],
        revision_id: int | None = None,
        comment: str | None = None,
    ):
        super().__init__(task_config, run_tracker)
        self.input_data = input_data
        self.bug_id = input_data.bug_id if isinstance(input_data, BugIdInput) else None
        self.plan_result = plan_result
        self.revision_id = revision_id
        self.comment = comment
        self.source_repo = source_repo
        self.fx_ctx = fx_ctx
        self.script_path = self.work_dir / "reproduction.mjs"

        if self.input_data.type == "bug_id":
            self.add_mcp_server("bugzilla", bugzilla_mcp_server, BUGZILLA_READ_TOOLS)

        self.add_mcp_server(
            "phabricator", phabricator_mcp_server, phabricator_read_tools
        )

        self.add_mcp_server(
            "firefox",
            build_sdk_server("firefox", fx_ctx, firefox.TOOLS),
            FIREFOX_BUILD_TOOLS,
        )

        self.add_mcp_server(
            "firefox-devtools",
            build_firefox_devtools_server(
                firefox_path=fx_ctx.binary,
                headless=task_config.headless,
                enable_script=True,
                enable_privileged_context=False,
            ),
            DEVTOOLS_TOOLS,
        )

        self.add_mcp_server(ACTIONS_SERVER_NAME, actions_server, action_tools)
        self.allowed_tools.extend(["Write", "Edit"])

    def subject(self) -> Any:
        if self.revision_id is not None:
            return f"D{self.revision_id}"
        return self.plan_result.url

    def agent_options(self) -> ClaudeAgentOptions:
        options = super().agent_options()
        options.cwd = str(self.source_repo.resolve())
        return options

    def user_prompt(self) -> str:
        webcompat_dir = self.source_repo / "browser/extensions/webcompat"
        interventions_dir = webcompat_dir / "data/interventions"
        tests_dir = self.source_repo / "testing/webcompat/interventions/tests"
        bug = self.bug_id if self.bug_id is not None else "<bug>"
        headless_flag = " --headless" if self.task_config.headless else ""
        run_script = as_shell(
            *script_command(
                self.script_path, self.fx_ctx.binary, self.task_config.headless
            ),
            cwd=self.script_path.parent,
        )
        fields: dict[str, object] = {
            "report": render_report(
                self.input_data,
                "Fetch it, with its comments and attachments, using the Bugzilla "
                "MCP tools, and request `cf_user_story` explicitly.",
            ),
            "instructions": (webcompat_dir / "AGENTS.md").read_text(),
            "interventions_dir": interventions_dir,
            "webcompat_dir": webcompat_dir,
            "tests_dir": tests_dir,
            "bug": bug,
            "run_tests": (
                f"./mach test-interventions --binary {self.fx_ctx.binary} "
                f"--bugs {bug}{headless_flag}"
            ),
            "script_path": self.script_path,
            "verify": PROMPTS.get(
                "verify.md", interventions_dir=interventions_dir, run_script=run_script
            ),
        }

        if self.revision_id is not None:
            return PROMPTS.get(
                "follow-up.md",
                **fields,
                revision_id=self.revision_id,
                comment=self.comment,
            )

        return PROMPTS.get(
            "intervention.md",
            **fields,
            url=self.plan_result.url,
            platforms=", ".join(self.plan_result.affected_platforms),
        )


class InterventionResults:
    def __init__(self, plan_result: InterventionPlanResult | None):
        self.plan_result = plan_result
        self.intervention_result: InterventionResult | None = None
        self.files_changed: list[str] = []

    def set_intervention(self, result: InterventionResult) -> None:
        if self.intervention_result is not None:
            raise ValueError("Got duplicate intervention results")
        self.intervention_result = result

    def into_result(self) -> AutowebcompatInterventionResult:
        intervention = self.intervention_result
        if intervention is None:
            intervention = InterventionResult(
                wrote_intervention=False,
                fixed_with_intervention=False,
                build_succeeded=False,
                summary="",
            )
        return AutowebcompatInterventionResult(
            wrote_intervention=intervention.wrote_intervention,
            fixed_with_intervention=intervention.fixed_with_intervention,
            build_succeeded=intervention.build_succeeded,
            failure_reason=intervention.failure_reason,
            summary=intervention.summary,
            plan_result=self.plan_result,
            files_changed=self.files_changed,
        )


async def run_autowebcompat_intervention(
    config: TaskConfig,
    tracker: RunTracker,
    input_data: AutoWebcompatInput,
    source_repo: Path,
    fx_ctx: FirefoxContext,
    bugzilla_mcp_server: McpServerConfig,
    phabricator_mcp_server: McpServerConfig,
    phabricator_read_tools: list[str],
    actions_recorder: ActionsRecorder | None,
    revision_id: int | None = None,
    comment: str | None = None,
) -> AutowebcompatInterventionResult:
    """Plan, reproduce, then write and verify an intervention for a bug.

    With ``revision_id``, follow up on that revision instead: address
    ``comment`` and update the revision or reply on it.
    """
    with Environment() as env:
        if not config.headless:
            env.start_xvfb()

        plan_result: InterventionPlanResult | None = None
        results = InterventionResults(None)

        # If it's a new intervention and not a followup, run intervention plan
        # and confirmation script to confirm the issue still present
        if revision_id is None:
            plan_task = InterventionPlan(
                config, tracker, input_data, bugzilla_mcp_server
            )
            plan_result = await plan_task.run()

            results = InterventionResults(plan_result)

            platforms = plan_result.affected_platforms
            if "linux" not in platforms:
                result = results.into_result()
                if platforms == ["ios"]:
                    result.failure_reason = "unsupported_ios"
                elif platforms == ["android"]:
                    result.failure_reason = "unsupported_android"
                else:
                    result.failure_reason = "unsupported_desktop_os"
                result.summary = (
                    f"The issue affects {', '.join(platforms)}; this agent can only "
                    "verify interventions on Linux desktop at the moment"
                )
                return result

            if plan_result.script_path is None:
                result = results.into_result()
                result.summary = "The report has no Puppeteer reproduction script"
                result.failure_reason = "no_puppeteer_script"
                return result

            firefox_browser = FirefoxBrowsers()
            repro_result = run_confirmation_script(
                plan_result.script_path, firefox_browser.nightly, config.headless
            )
            if not repro_result.reproduced:
                result = results.into_result()
                result.summary = repro_result.summary
                result.failure_reason = "not_reproducible"
                return result

        write_mozconfig(fx_ctx)

        action_types = (
            INTERVENTION_ACTIONS if revision_id is None else FOLLOW_UP_ACTIONS
        )
        actions_recorder, actions_server = actions_server_for(
            actions_recorder, types=action_types
        )

        intervention_task = Intervention(
            config,
            tracker,
            input_data,
            plan_result,
            source_repo,
            fx_ctx,
            bugzilla_mcp_server,
            phabricator_mcp_server,
            phabricator_read_tools,
            actions_server,
            actions_to_tool_names(action_types),
            revision_id=revision_id,
            comment=comment,
        )
        base = git(source_repo, "rev-parse", "HEAD").strip()

        def reject_unexpected_changes(action: dict) -> None:
            unexpected = unexpected_changes(changed_files(source_repo, base))
            if unexpected:
                raise ToolError(
                    "An intervention patch may only change files under "
                    f"{' and '.join(ALLOWED_CHANGE_PATHS)}. "
                    f"These are outside it: {', '.join(unexpected)}. Revert them, "
                    "then either fix the issue with an intervention within allowed "
                    "directories or report `no_intervention_possible` without submitting a patch."
                )

        for action_type in PATCH_ACTION_TYPES:
            actions_recorder.add_hook(action_type, reject_unexpected_changes)

        intervention_result = await intervention_task.run()
        results.files_changed = changed_files(source_repo, base)

        if revision_id is None:
            script_path = plan_result.script_path
            to_verify = intervention_result.fixed_with_intervention
        else:
            script_path = intervention_task.script_path
            to_verify = any(
                action["type"] in PATCH_ACTION_TYPES
                for action in actions_recorder.list_actions()
            )

        unexpected = unexpected_changes(results.files_changed)
        failure_reason = None
        if unexpected:
            logger.warning("Patch touches unexpected files: %s", unexpected)
            failure_reason = "unexpected_changes"
        elif to_verify and (
            not script_path.exists()
            or run_script(script_path, fx_ctx.binary, config.headless) != 0
        ):
            logger.warning("Reproduction script did not confirm the intervention")
            failure_reason = "verification_failed"

        if failure_reason:
            intervention_result.fixed_with_intervention = False
            intervention_result.failure_reason = failure_reason
            patch_dropped = False
            for action in actions_recorder.list_actions():
                if action["type"] in PATCH_ACTION_TYPES:
                    actions_recorder.remove_action(action["action_id"])
                    patch_dropped = True

            # What a follow-up tells the reviewer when its update is dropped after the
            # agent finished, so the mention does not go unanswered.
            dropped_updates_explanation = {
                "verification_failed": (
                    "I made a change for the comments above, but the reproduction script "
                    "didn't pass against the build with it, so I haven't updated the revision."
                ),
                "unexpected_changes": (
                    "I made a change for the comments above, but it touched files outside "
                    f"{' and '.join(ALLOWED_CHANGE_PATHS)}, so I haven't updated the revision."
                ),
            }

            if revision_id is not None and patch_dropped:
                actions_recorder.record(
                    "phabricator.add_comment",
                    {
                        "revision_id": revision_id,
                        "text": dropped_updates_explanation[failure_reason],
                    },
                    reasoning=f"The update was dropped: {failure_reason}.",
                )

        results.set_intervention(intervention_result)

    return results.into_result()
