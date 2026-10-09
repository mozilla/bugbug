"""Firefox web-compatibility intervention agent.

Plans an intervention for a broken site based on existing reproduction script,
then writes the intervention and verifies it against the same script. The bug is
passed either inline as ``bug_data`` text or a Bugzilla ``bug_id`` (read via
Bugzilla broker).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
from autowebcompat_tools.environment import Environment
from autowebcompat_tools.inputs import AutoWebcompatInput, BugDataInput, BugIdInput
from autowebcompat_tools.task import RunTracker, Task, TaskConfig
from claude_agent_sdk import ClaudeAgentOptions, McpServerConfig
from claude_agent_sdk.types import SystemPromptPreset
from hackbot_runtime import ActionsRecorder
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.claude_sdk import actions_server_for, actions_to_tool_names
from pydantic import BaseModel

from .browser import FirefoxBrowsers
from .config import (
    BUGZILLA_READ_TOOLS,
    DEVTOOLS_TOOLS,
    FIREFOX_BUILD_TOOLS,
    INTERVENTION_ACTIONS,
    INTERVENTION_TESTS_DIR,
    INTERVENTIONS_DIR,
    SOURCE_WRITE_TOOLS,
)
from .mcp_servers import build_firefox_devtools_server
from .result import (
    RESULT_SERVER_NAME,
    InterventionPlanResult,
    InterventionResult,
    ReproScriptResult,
)

logger = logging.getLogger("autowebcompat-intervention")

HERE = Path(__file__).resolve().parent

WORK_DIR = Path("/app/autowebcompat-intervention")
NODE_MODULES = WORK_DIR / "node_modules"

PROMPTS = HERE / "prompts"

# Claude Code's system prompt for coding agents plus some rules and reporting
SYSTEM_PROMPT: SystemPromptPreset = {
    "type": "preset",
    "preset": "claude_code",
    "append": (PROMPTS / "rules.md").read_text(),
}


def render_prompt(name: str, **fields: object) -> str:
    """Render a prompt template from ``prompts/`` via ``str.format``."""
    return (PROMPTS / name).read_text().format(**fields)


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
    # None for an inline bug_data run, which has no Bugzilla bug behind it.
    bug_id: int | None
    plan: InterventionPlanResult
    result: InterventionResult


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


def run_script(script_path: Path, firefox_path: Path, headless: bool) -> int | None:
    """Run the reproduction script against one Firefox; return its exit code.

    Returns ``None`` when the script gave no verdict -- it timed out, or it
    exited without printing the verdict line.
    Otherwise, follows the repro-script contract used across the autowebcompat
    agents: 0 = the functionality worked, 1 = the breakage reproduced.
    """
    env = {
        **os.environ,
        "NODE_PATH": str(NODE_MODULES),
        "BROWSER": "firefox",
        "BROWSER_BIN": str(firefox_path),
    }
    if headless:
        env["HEADLESS"] = "1"

    script_timeout = 5 * 60

    try:
        proc = subprocess.run(
            ["node", str(script_path)],
            cwd=script_path.parent,
            env=env,
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


class InterventionPlan(Task[InterventionPlanResult]):
    """Read the bug, download its repro script, and propose an intervention type."""

    name = "intervention_plan"
    result_cls = InterventionPlanResult
    result_server_name = RESULT_SERVER_NAME

    def system_prompt(self) -> SystemPromptPreset:
        return SYSTEM_PROMPT

    work_dir = WORK_DIR

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
        return render_prompt(
            "plan.md",
            report=render_report(
                self.input_data,
                "Fetch it using the Bugzilla MCP tools and request `cf_user_story` "
                "explicitly: it is not in the default field set.",
            ),
            script_path=self.script_path,
        )


class Intervention(Task[InterventionResult]):
    name = "intervention"
    result_cls = InterventionResult
    result_server_name = RESULT_SERVER_NAME

    def system_prompt(self) -> SystemPromptPreset:
        return SYSTEM_PROMPT

    work_dir = WORK_DIR

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        input_data: AutoWebcompatInput,
        plan_result: InterventionPlanResult,
        source_repo: Path,
        fx_ctx: FirefoxContext,
        bugzilla_mcp_server: McpServerConfig,
        phabricator_mcp_server: McpServerConfig,
        phabricator_read_tools: list[str],
        actions_server: McpServerConfig,
        action_tools: list[str],
    ):
        super().__init__(task_config, run_tracker)
        self.input_data = input_data
        self.bug_id = input_data.bug_id if isinstance(input_data, BugIdInput) else None
        self.plan_result = plan_result
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
        self.allowed_tools.extend(SOURCE_WRITE_TOOLS)

    def subject(self) -> Any:
        return self.plan_result.url

    def agent_options(self) -> ClaudeAgentOptions:
        options = super().agent_options()
        options.cwd = str(self.source_repo.resolve())
        return options

    def user_prompt(self) -> str:
        interventions_dir = self.source_repo / INTERVENTIONS_DIR
        webcompat_dir = interventions_dir.parent.parent
        tests_dir = self.source_repo / INTERVENTION_TESTS_DIR
        bug = self.bug_id if self.bug_id is not None else "<bug>"
        headless_env = "HEADLESS=1 " if self.task_config.headless else ""
        headless_flag = " --headless" if self.task_config.headless else ""
        run_script = (
            f"cd {self.work_dir} && BROWSER=firefox "
            f"BROWSER_BIN={self.fx_ctx.binary} {headless_env}node {self.script_path}"
        )
        run_tests = (
            f"./mach test-interventions --binary {self.fx_ctx.binary} "
            f"--bugs {bug}{headless_flag}"
        )

        return render_prompt(
            "intervention.md",
            url=self.plan_result.url,
            report=render_report(
                self.input_data,
                "Fetch it, with its comments and attachments, using the Bugzilla "
                "MCP tools, and request `cf_user_story` explicitly.",
            ),
            platforms=", ".join(self.plan_result.affected_platforms),
            instructions=(webcompat_dir / "AGENTS.md").read_text(),
            interventions_dir=interventions_dir,
            webcompat_dir=webcompat_dir,
            tests_dir=tests_dir,
            bug=bug,
            script_path=self.script_path,
            run_script=run_script,
            run_tests=run_tests,
        )


class InterventionResults:
    def __init__(self, bug_id: int | None, plan_result: InterventionPlanResult):
        self.bug_id = bug_id
        self.plan_result = plan_result
        self.intervention_result: InterventionResult | None = None

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
            bug_id=self.bug_id,
            plan=self.plan_result,
            result=intervention,
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
) -> AutowebcompatInterventionResult:
    """Plan, reproduce, then write and verify an intervention for a bug."""
    with Environment() as env:
        if not config.headless:
            env.start_xvfb()

        plan_task = InterventionPlan(config, tracker, input_data, bugzilla_mcp_server)
        plan_result = await plan_task.run()

        bug_id = input_data.bug_id if isinstance(input_data, BugIdInput) else None
        results = InterventionResults(bug_id, plan_result)

        platforms = plan_result.affected_platforms
        if "linux" not in platforms:
            result = results.into_result()
            if platforms == ["ios"]:
                result.result.failure_reason = "unsupported_ios"
            elif platforms == ["android"]:
                result.result.failure_reason = "unsupported_android"
            else:
                result.result.failure_reason = "unsupported_desktop_os"
            result.result.summary = (
                f"The issue affects {', '.join(platforms)}; this agent can only "
                "verify interventions on Linux desktop at the moment"
            )
            return result

        if plan_result.script_path is None:
            result = results.into_result()
            result.result.summary = "The report has no Puppeteer reproduction script"
            result.result.failure_reason = "no_puppeteer_script"
            return result

        firefox_browser = FirefoxBrowsers()
        repro_result = run_confirmation_script(
            plan_result.script_path, firefox_browser.nightly, config.headless
        )
        if not repro_result.reproduced:
            result = results.into_result()
            result.result.summary = repro_result.summary
            result.result.failure_reason = "not_reproducible"
            return result

        write_mozconfig(fx_ctx)

        actions_recorder, actions_server = actions_server_for(
            actions_recorder, types=INTERVENTION_ACTIONS
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
            actions_to_tool_names(INTERVENTION_ACTIONS),
        )
        intervention_result = await intervention_task.run()

        if (
            intervention_result.fixed_with_intervention
            and run_script(plan_result.script_path, fx_ctx.binary, config.headless) != 0
        ):
            logger.warning("Reproduction script did not confirm the intervention")
            intervention_result.fixed_with_intervention = False
            intervention_result.failure_reason = "verification_failed"
            for action in actions_recorder.list_actions():
                if action["type"] in INTERVENTION_ACTIONS:
                    actions_recorder.remove_action(action["action_id"])

        results.set_intervention(intervention_result)

    return results.into_result()
