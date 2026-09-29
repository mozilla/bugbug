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
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, Literal, Self

from agent_tools import firefox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.firefox import FirefoxContext
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import ActionsRecorder, AgentError
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.claude_sdk import actions_server_for, actions_to_tool_names
from hackbot_runtime.claude import Reporter
from pydantic import BaseModel

from .browser import FirefoxBrowsers
from .config import (
    BUGZILLA_READ_TOOLS,
    DEVTOOLS_TOOLS,
    FIREFOX_BUILD_TOOLS,
    INTERVENTION_ACTIONS,
    INTERVENTIONS_DIR,
    SOURCE_WRITE_TOOLS,
)
from .mcp_servers import build_firefox_devtools_server
from .result import (
    RESULT_SERVER_NAME,
    SUBMIT_RESULT_TOOL,
    InterventionPlanResult,
    InterventionResult,
    ReproScriptResult,
    ResultCollector,
    ResultT,
    build_result_server,
)

logger = logging.getLogger("autowebcompat-intervention")

HERE = Path(__file__).resolve().parent

# Where the pinned npm deps (puppeteer, the DevTools MCP server) are installed
# in the image. The reproduction script is downloaded here too, beside
# node_modules: ESM `import` resolves by walking up from the script's own
# directory, so a script run from anywhere else cannot find puppeteer. It also
# keeps the script out of the source checkout, so it never lands in the patch.
WORK_DIR = Path("/app/autowebcompat-intervention")
NODE_MODULES = WORK_DIR / "node_modules"

# A repro script gets five minutes to reach a verdict, matching repro and
# diagnosis. Longer than that is a hung script, not a slow site.
SCRIPT_TIMEOUT = 5 * 60


@dataclass
class BugIdInput:
    bug_id: int
    type: Literal["bug_id"] = "bug_id"

    def subject(self) -> str:
        return f"bug {self.bug_id}"


@dataclass
class BugDataInput:
    bug_data: str
    type: Literal["bug_data"] = "bug_data"

    def subject(self) -> str:
        return self.bug_data


AutoWebcompatInput = BugIdInput | BugDataInput


class AutowebcompatInterventionResult(BaseModel):
    # None for an inline bug_data run, which has no Bugzilla bug behind it.
    bug_id: int | None
    plan: InterventionPlanResult
    result: InterventionResult


@dataclass
class TaskConfig:
    model: str | None = None
    max_turns: int | None = None
    effort: (
        Literal["low"]
        | Literal["medium"]
        | Literal["high"]
        | Literal["xhigh"]
        | Literal["max"]
        | None
    ) = None
    log: Path | None = None
    verbose: bool = True
    headless: bool = False


@dataclass
class TaskRun:
    name: str
    start_time: datetime
    end_time: datetime
    num_turns: int
    total_cost_usd: float | None


class RunTracker:
    """Accumulates per-task turn counts and costs across the run's stages."""

    def __init__(self) -> None:
        self.task_runs: list[TaskRun] = []
        self.current_task: tuple[str, datetime] | None = None

    @property
    def num_turns(self) -> int:
        return sum(item.num_turns for item in self.task_runs)

    @property
    def total_cost_usd(self) -> float:
        return sum(
            item.total_cost_usd
            for item in self.task_runs
            if item.total_cost_usd is not None
        )

    def start_task(self, name: str) -> None:
        self.current_task = name, datetime.now()

    def end_task(self, name: str, result_msg: ResultMessage) -> None:
        if self.current_task is None:
            logger.warning("Got end_task without start_task")
            return
        current_name, start_time = self.current_task
        if current_name != name:
            logger.warning(
                "Got end_task with name %s but current_task was %s", name, current_name
            )
            self.current_task = None
            return
        self.task_runs.append(
            TaskRun(
                name=name,
                start_time=start_time,
                end_time=datetime.now(),
                num_turns=result_msg.num_turns,
                total_cost_usd=result_msg.total_cost_usd,
            )
        )


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


class Environment:
    """Background processes the run needs, torn down on exit."""

    def __init__(self) -> None:
        self.started_processes: list[subprocess.Popen] = []

    def start(self, cmd: list[str]) -> None:
        logger.info("Running %s", " ".join(cmd))
        self.started_processes.append(subprocess.Popen(cmd))

    def start_xvfb(self) -> None:
        self.start(
            [
                "Xvfb",
                os.environ["DISPLAY"],
                "-screen",
                "0",
                "%sx%sx%s"
                % (
                    os.environ["SCREEN_WIDTH"],
                    os.environ["SCREEN_HEIGHT"],
                    os.environ["SCREEN_DEPTH"],
                ),
            ]
        )
        self.start(["fluxbox", "-display", os.environ["DISPLAY"]])

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args, **kwargs) -> None:
        for process in self.started_processes:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()


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

    try:
        proc = subprocess.run(
            ["node", str(script_path)],
            cwd=script_path.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.warning("repro script timed out after %ss", SCRIPT_TIMEOUT)
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


class Task(ABC, Generic[ResultT]):
    """One agent session, ending in a validated ``submit_result`` payload."""

    name: str = "unnamed-task"
    result_server_name: str = RESULT_SERVER_NAME
    submit_result_tool: str = SUBMIT_RESULT_TOOL
    result_cls: type[ResultT]

    def __init__(self, task_config: TaskConfig, run_tracker: RunTracker):
        self.task_config = task_config
        self.run_tracker = run_tracker
        self.allowed_tools = [
            "Read",
            "Grep",
            "Glob",
            "Bash",
            self.submit_result_tool,
        ]

        self.result_collector = ResultCollector(self.result_cls)
        self.mcp_servers = {}

        result_server = self.result_server()
        if result_server is not None:
            self.mcp_servers[self.result_server_name] = result_server

    def add_mcp_server(
        self, name: str, server: McpServerConfig, tools: list[str]
    ) -> None:
        self.mcp_servers[name] = server
        self.allowed_tools.extend(tools)

    def result_server(self) -> McpServerConfig | None:
        return build_result_server(self.result_collector)

    def system_prompt(self) -> str:
        return (HERE / "prompts" / "system.md").read_text()

    @abstractmethod
    def user_prompt(self) -> str: ...

    @abstractmethod
    def subject(self) -> Any: ...

    def agent_options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            system_prompt=self.system_prompt(),
            mcp_servers=self.mcp_servers,
            permission_mode="bypassPermissions",
            allowed_tools=self.allowed_tools,
            model=self.task_config.model,
            max_turns=self.task_config.max_turns,
            setting_sources=[],
            # DevTools snapshots of complex pages can exceed the SDK's default
            # 1 MiB message buffer, which kills the reader fatally.
            max_buffer_size=10 * 1024 * 1024,
            effort=self.task_config.effort,
        )

    async def run(self) -> ResultT:
        self.run_tracker.start_task(self.name)
        subject = self.subject()
        preview = str(subject)
        if len(preview) > 200:
            preview = f"{preview[:200]}..."
        logger.info("Running %s with %s", self.__class__.__name__, preview)

        result_msg: ResultMessage | None = None
        with Reporter(
            verbose=self.task_config.verbose, log_path=self.task_config.log
        ) as reporter:
            reporter.header(subject)
            async with ClaudeSDKClient(options=self.agent_options()) as client:
                await client.query(self.user_prompt())
                async for msg in client.receive_response():
                    reporter.message(msg)
                    if isinstance(msg, ResultMessage):
                        result_msg = msg

        if result_msg is None:
            raise AgentError(f"{subject}: agent produced no result message")
        self.run_tracker.end_task(self.name, result_msg)
        if result_msg.is_error:
            raise AgentError(
                f"{subject} intervention failed: "
                f"{result_msg.result or result_msg.subtype}"
            )
        if self.result_collector.result is None:
            raise AgentError(
                f"{subject}: agent finished without submitting a result via submit_result"
            )
        return self.result_collector.result


class InterventionPlan(Task[InterventionPlanResult]):
    """Read the bug, download its repro script, and propose an intervention type."""

    name = "intervention_plan"
    result_cls = InterventionPlanResult
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

    def system_prompt(self) -> str:
        return (
            super()
            .system_prompt()
            .format(
                task_details=f"""
1. Read the report and every comment. Identify the affected URL, and record
   in `issue_details` the reported broken behavior, the conditions it happens under,
   and any evidence or potential solutions given in the report or comments.

2. Retrieve the Puppeteer reproduction script (an mjs file, typically titled
   `Reproduction script generated by autowebcompat bot`) and download it
   to exactly this path: {self.script_path}.

3. If there is no reproduction script, set `script_path` to null. Do not
   substitute a testcase or write a script yourself.

4. Determine which platforms the issue affects:
   - Is it described as affecting iOS? If so it is unlikely to affect others.
   - If not iOS, does it affect desktop and Android, or only one?
   - If it affects desktop, is there evidence it is OS-specific? An issue is
     only OS-specific if the report states it did not reproduce elsewhere —
     reporters often test just one OS.

5. Submit your findings via `submit_result` (see "Reporting your result").
"""
            )
        )

    def user_prompt(self) -> str:
        if isinstance(self.input_data, BugDataInput):
            return (
                "Here is the web-compatibility report to work on:\n\n"
                f"{self.input_data.bug_data}\n\n"
                "Follow your task procedure."
            )
        return (
            "The web-compatibility report to work on is Bugzilla bug "
            f"{self.input_data.bug_id}.\n\n"
            "Fetch it using the Bugzilla MCP tools, then follow your task "
            "procedure."
        )


class Intervention(Task[InterventionResult]):
    name = "intervention"
    result_cls = InterventionResult
    work_dir = WORK_DIR

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        bug_id: int | None,
        plan_result: InterventionPlanResult,
        source_repo: Path,
        fx_ctx: FirefoxContext,
        phabricator_mcp_server: McpServerConfig,
        phabricator_read_tools: list[str],
        actions_server: McpServerConfig,
        action_tools: list[str],
    ):
        super().__init__(task_config, run_tracker)
        self.bug_id = bug_id
        self.plan_result = plan_result
        self.source_repo = source_repo
        self.fx_ctx = fx_ctx
        self.script_path = self.work_dir / "reproduction.mjs"

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

    def system_prompt(self) -> str:
        interventions_dir = self.source_repo / INTERVENTIONS_DIR
        webcompat_dir = interventions_dir.parent.parent
        headless_env = "HEADLESS=1 " if self.task_config.headless else ""
        run_script = (
            f"cd {self.work_dir} && BROWSER=firefox "
            f"BROWSER_BIN={self.fx_ctx.binary} {headless_env}node {self.script_path}"
        )
        return (
            super()
            .system_prompt()
            .format(
                task_details=f"""
Your working directory is a Firefox source checkout. The breakage has already
been reproduced, so your job is to add an intervention and prove it fixes
the issue.

1. Find an existing intervention in {interventions_dir} that solves a similar
   problem and follow its shape. {webcompat_dir}/intervention_schema.json is
   the authoritative schema; {webcompat_dir}/codegen.py validates against it at
   build time, so a schema violation fails the build.

2. Write the intervention, then build Firefox with the `build_firefox` tool.
   The build is your schema check — if it fails, read the error; it usually names the offending field.
   Every file codegen writes to `injections/generated/` must be listed in
   `preprocessed_intervention_files.mozbuild`, and stale entries removed; if
   not, the build error lists exactly which names to add or remove. If you're unable to build it, report
   `build_failed` rather than packaging the add-on by hand. After a rebuild,
   call `restart_firefox` before using the DevTools tools again.

3. Re-run the reproduction script against your build: {run_script} from {self.work_dir} - it must exit with 0.
   If it still exits with 1, iterate or report `no_intervention_possible` — never report
   a fix you did not observe.

4. If the script exits 0, record `phabricator_submit_patch` titled
   `Bug <id> - Add webcompat intervention for <domain>`. Recording does not
   change Phabricator during the run, but the recorded patch is applied
   afterwards, so treat it as final. Fill in `reasoning` properly: it is kept
   as the audit note for the submission.

5. Submit your findings via `submit_result` (see "Reporting your result").
"""
            )
        )

    def user_prompt(self) -> str:
        bug_note = f"Bugzilla bug: {self.bug_id}\n\n" if self.bug_id is not None else ""
        affects_os = self.plan_result.affects_os
        os_note = (
            ""
            if affects_os in (None, "all")
            else f" (desktop OSes: {', '.join(affects_os)})"
        )
        return (
            f"Write a webcompat intervention for {self.plan_result.url}.\n\n"
            f"{bug_note}"
            f"Issue details:\n{self.plan_result.issue_details}\n\n"
            f"Affected platforms: {', '.join(self.plan_result.affects_platforms)}"
            f"{os_note} — use this for the intervention's `platforms` field.\n\n"
            "Follow your task procedure."
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

        platforms = plan_result.affects_platforms
        if "desktop" not in platforms:
            result = results.into_result()
            result.result.summary = (
                "The agent only supports desktop platforms at the moment"
            )
            result.result.failure_reason = (
                "unsupported_ios" if platforms == ["ios"] else "unsupported_android"
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
            bug_id,
            plan_result,
            source_repo,
            fx_ctx,
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
