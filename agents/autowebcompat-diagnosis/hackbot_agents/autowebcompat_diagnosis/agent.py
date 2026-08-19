"""Firefox web-compatibility diagnosis agent."""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Literal

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import AgentError
from hackbot_runtime.claude import Reporter
from pydantic import BaseModel

from .browser import ChromeBrowsers, FirefoxBrowsers
from .config import BUGZILLA_READ_TOOLS, CHROME_DEVTOOLS_TOOLS, DEVTOOLS_TOOLS
from .mcp_servers import build_chrome_devtools_server, build_firefox_devtools_server
from .result import (
    RESULT_SERVER_NAME,
    SUBMIT_RESULT_TOOL,
    DiagnosisPlanResult,
    DiagnosisResult,
    ReproScriptResult,
    ResultCollector,
    ResultT,
    build_result_server,
)

HERE = Path(__file__).resolve().parent

# Where the pinned npm deps (puppeteer, the DevTools MCP servers) are installed
# in the image; the agent runs the reproduction script with this on NODE_PATH so
# its `import puppeteer` resolves.
WORK_DIR = Path("/app/diagnosis")
NODE_MODULES = WORK_DIR / "node_modules"

logger = logging.getLogger("autowebcompat-diagnosis")

PublishFile = Callable[[str, Path, str | None], str]


class FirefoxChannel(Enum):
    nightly = "nightly"
    stable = "stable"
    esr = "esr"


@dataclass
class BugIdInput:
    bug_id: int
    type: Literal["bug_id"] = "bug_id"

    def subject(self) -> str:
        return f" bug {self.bug_id}"


@dataclass
class BugDataInput:
    bug_data: str
    type: Literal["bug_data"] = "bug_data"

    def subject(self) -> str:
        return self.bug_data


AutoWebcompatInput = BugIdInput | BugDataInput


class AutowebcompatDiagnosisResult(BaseModel):
    reproduced: bool
    failure_reason: str | None
    root_cause: str | None
    evidence: str | None
    testcase_url: str | None


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


@dataclass
class TaskRun:
    name: str
    start_time: datetime
    end_time: datetime
    num_turns: int
    total_cost_usd: float | None


class RunTracker:
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


class Task(ABC, Generic[ResultT]):
    name: str = "unnamed-task"
    result_server_name: str = RESULT_SERVER_NAME
    submit_result_tool: str = SUBMIT_RESULT_TOOL
    result_cls: type[ResultT]

    def __init__(self, task_config: TaskConfig, run_tracker: RunTracker):
        self.task_config = task_config
        self.run_tracker = run_tracker
        self.allowed_tools = [
            "Read",
            "Write",
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
            # DevTools snapshots of complex pages serialize to JSON that can
            # exceed the SDK's default 1 MiB message buffer (the reader dies
            # fatally if it does). Raise it well above that ceiling.
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
                f"{subject} diagnosis failed: {result_msg.result or result_msg.subtype}"
            )
        if self.result_collector.result is None:
            raise AgentError(
                f"{subject}: agent finished without submitting a result via submit_result"
            )
        return self.result_collector.result


def run_script(script_path: Path, browser: str, browser_path: Path) -> int | None:
    """Run the reproduction script in one browser; return its exit code.

    Returns ``None`` if the script timed out, i.e. gave no verdict.
    """
    script_timeout = 5 * 60
    try:
        proc = subprocess.run(
            ["node", str(script_path)],
            env={
                **os.environ,
                "NODE_PATH": str(NODE_MODULES),
                "BROWSER": browser,
                "BROWSER_BIN": str(browser_path),
            },
            capture_output=True,
            text=True,
            timeout=script_timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("%s run timed out after %ss", browser, script_timeout)
        return None

    logger.info(
        "%s run exited %s\nstdout:\n%s\nstderr:\n%s",
        browser,
        proc.returncode,
        proc.stdout,
        proc.stderr,
    )
    return proc.returncode


def run_confirmation_script(
    script_path: Path, firefox_path: Path, chrome_path: Path
) -> ReproScriptResult | None:
    """Check the script still demonstrates the difference, without an agent.

    The difference is demonstrated when the Firefox run exits 1 (not working)
    and the Chrome run exits 0 (working). Returns ``None`` for any other
    outcome — wrong exit codes, a script error, or a timeout — so the caller
    can fall back to the agent task.
    """
    firefox_code = run_script(script_path, "firefox", firefox_path)
    if firefox_code != 1:
        return None
    chrome_code = run_script(script_path, "chrome", chrome_path)
    if chrome_code != 0:
        return None

    return ReproScriptResult(
        reproduced=True,
        failure_reason=None,
        summary=(
            "The Puppeteer reproduction script attached to the bug still "
            "demonstrates the difference: the Firefox run exited 1 (not "
            "working) and the Chrome run exited 0 (working)."
        ),
        script_path=script_path,
    )


def make_empty_temp_file(dir: Path, prefix: str | None, suffix: str) -> Path:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=dir)
    f = os.fdopen(fd)
    f.close()
    return Path(path)


class DiagnosisPlan(Task):
    name = "diagnosis_plan"
    result_cls = DiagnosisPlanResult
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
1. Identify the affected URL and the reproduction steps from the report.

2. Choose the Firefox channel to diagnose on, either from the channels listed in the
   user_story field (a line like `autowebcompat-repro-channels:nightly,stable,esr`)
   or from report text, if there is no user_story available.
   When a Bugzilla bug id is passed, request it explicitly: `cf_user_story` is not
   in the default field set. Prefer `nightly` if it is in the list,
   otherwise pick first listed channel. Default to `nightly` if there is no evidence
   of the affected channel in the report.

3. If a Puppeteer reproduction script is attached to the bug (an mjs file,
   typically named `Reproduction script generated by autowebcompat bot`),
   download it to exactly:
     {self.script_path}.

4. Submit your findings via `submit_result` (see "Reporting your result").
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
        if isinstance(self.input_data, BugIdInput):
            return (
                f"The web-compatibility report to work on is Bugzilla bug {self.input_data.bug_id}. "
                "Fetch it using the Bugzilla MCP tools, then follow your task procedure."
            )


class ReproScript(Task):
    name = "repro_script"
    result_cls = ReproScriptResult
    work_dir = WORK_DIR

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        firefox_path: Path,
        chrome_path: Path,
        plan_result: DiagnosisPlanResult,
    ):
        super().__init__(task_config, run_tracker)
        self.firefox_path = firefox_path
        self.chrome_path = chrome_path
        self.plan_result = plan_result
        self.script_path = self.work_dir / "reproduction.mjs"
        self.add_mcp_server(
            "firefox-devtools",
            build_firefox_devtools_server(
                firefox_path=firefox_path,
                headless=True,
                enable_script=True,
                enable_privileged_context=False,
            ),
            DEVTOOLS_TOOLS,
        )
        self.add_mcp_server(
            "chrome-devtools",
            build_chrome_devtools_server(chrome_path=chrome_path, headless=True),
            CHROME_DEVTOOLS_TOOLS,
        )

    def subject(self) -> Any:
        return self.plan_result.url

    def system_prompt(self) -> str:
        repro_reference = self.work_dir / "repro_reference.mjs"
        script_state = (
            f"""A reproduction script was attached to the bug and downloaded to
   `{self.plan_result.script_path}`, but it has already been run in both
   browsers and no longer demonstrates the difference. Read it and use it as a starting point, but
   expect to fix or rewrite it."""
            if self.plan_result.script_path is not None
            else """Create a Puppeteer script that demonstrates the difference."""
        )
        return (
            super()
            .system_prompt()
            .format(
                task_details=f"""
You are establishing whether the issue still reproduces, and getting a Puppeteer
script that demonstrates it. Do not investigate why the difference happens.

1. Confirm the issue: run the reproduction steps against the reported
   site in Firefox with the Firefox DevTools MCP (headless, as is every browser
   on this system), then run the same steps in Chrome with the Chrome DevTools
   MCP.
   - A genuine web-compat issue reproduces in Firefox but not in Chrome. If the
     behavior is identical in both, set `failure_reason` to `non_compat`.
   - Reproduce against the actual reported site. If you cannot reach it — it is
     behind a login wall, blocked, gated by a captcha, or down — report
     `reproduced` as false with the appropriate `failure_reason` and stop.

2. {script_state}
   Follow the spec in `{repro_reference}` (read the file before writing), write
   your script to exactly `{self.script_path}`, and run it in both browsers:

   `NODE_PATH={NODE_MODULES} BROWSER=firefox BROWSER_BIN={self.firefox_path} node {self.script_path}`
   `NODE_PATH={NODE_MODULES} BROWSER=chrome BROWSER_BIN={self.chrome_path} node {self.script_path}`

   The script checks one browser per run: the difference is demonstrated when
   the Firefox run exits with 1 (not working) and the Chrome run exits with 0
   (working). Revise and re-run until both runs execute cleanly and show that
   difference, then set `script_path` to that path.

   If you're unable to get there, leave `script_path` null. That does not by
   itself mean the issue failed to reproduce: judge that on the evidence you
   gathered in step 1.

3. Submit your findings via `submit_result` (see "Reporting your result").
"""
            )
        )

    def user_prompt(self) -> str:
        return f"""The issue to reproduce is on {self.plan_result.url}

Here are the reported steps to reproduce it:
{self.plan_result.steps}"""


class Diagnosis(Task):
    name = "diagnosis"
    result_cls = DiagnosisResult
    work_dir = WORK_DIR

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        firefox_path: Path,
        chrome_path: Path,
        plan_result: DiagnosisPlanResult,
        repro_result: ReproScriptResult,
    ):
        super().__init__(task_config, run_tracker)
        self.firefox_path = firefox_path
        self.chrome_path = chrome_path
        self.plan_result = plan_result
        self.repro_result = repro_result
        self.testcase_path = make_empty_temp_file(self.work_dir, "testcase=", ".html")
        self.add_mcp_server(
            "firefox-devtools",
            build_firefox_devtools_server(
                firefox_path=firefox_path,
                headless=True,
                enable_script=True,
                enable_privileged_context=False,
            ),
            DEVTOOLS_TOOLS,
        )
        self.add_mcp_server(
            "chrome-devtools",
            build_chrome_devtools_server(chrome_path=chrome_path, headless=True),
            CHROME_DEVTOOLS_TOOLS,
        )

    def subject(self) -> Any:
        return self.plan_result.url

    def system_prompt(self) -> str:
        script_path = self.repro_result.script_path
        script_step = (
            f"""1. Read the Puppeteer reproduction script (it drives the real site in both
   browsers):
     {script_path}
   You may re-run it to observe the difference:

   `NODE_PATH={NODE_MODULES} BROWSER=firefox BROWSER_BIN={self.firefox_path} node {script_path}`
   `NODE_PATH={NODE_MODULES} BROWSER=chrome BROWSER_BIN={self.chrome_path} node {script_path}`

   It exits with 1 in Firefox (broken) and 0 in Chrome (working)."""
            if script_path is not None
            else """1. Drive the reported site in both browsers with
            the DevTools tools, following the reproduction steps, to observe the difference."""
        )
        return (
            super()
            .system_prompt()
            .format(
                task_details=f"""
Diagnose the root cause of the reported issue, using the reproduction findings
as your starting evidence.

{script_step}

2. Investigate why Firefox differs from Chrome. Use the Firefox and Chrome
   DevTools tools to compare the two browsers on the reported site and
   isolate the divergence, then form a root-cause hypothesis based on that evidence.

3. If the difference between the browsers is not a browser engine
   implementation difference, but due to the site explicitly switching behaviours
   between browsers (e.g. through UA sniffing or other browser-specific codepaths)
   then leave `testcase_path` null.

4. Otherwise, the difference is a browser engine implementation difference. In this case
   create a minimal reduced test case that reproduces the difference between the
   browsers and write it to exactly this path: {self.testcase_path}.
   The test case must include an inline explanation (a comment or on-page text)
   of what should happen and how Firefox differs from Chrome. Then load that
   file in both Firefox and Chrome via the DevTools tools and confirm it
   reproduces the same difference; if it does not, revise it until it does. If
   you cannot produce the testcase, leave `testcase_path` null.

4. Submit your diagnosis via `submit_result` (see "Reporting your result"). Do
   not propose a fix.
"""
            )
        )

    def user_prompt(self) -> str:
        return f"""The issue to diagnose is on {self.plan_result.url}
It was confirmed to reproduce in Firefox but not Chrome.

Here are the steps to reproduce it:
{self.plan_result.steps}"""


class DiagnosisResults:
    def __init__(self, publish_file: PublishFile, repro_result: ReproScriptResult):
        self.publish_file = publish_file
        self.repro_result = repro_result
        self.diagnosis_result: DiagnosisResult | None = None

    @property
    def testcase_url(self) -> str | None:
        if self.diagnosis_result is None or self.diagnosis_result.testcase_path is None:
            return None
        return self.publish_file(
            "testcase.html", self.diagnosis_result.testcase_path, "text/html"
        )

    def set_diagnosis(self, result: DiagnosisResult) -> None:
        if self.diagnosis_result is not None:
            raise ValueError("Got duplicate diagnosis results")
        self.diagnosis_result = result

    def into_result(self) -> AutowebcompatDiagnosisResult:
        diagnosis = self.diagnosis_result
        return AutowebcompatDiagnosisResult(
            reproduced=self.repro_result.reproduced,
            failure_reason=self.repro_result.failure_reason,
            root_cause=diagnosis.root_cause if diagnosis is not None else None,
            evidence=diagnosis.evidence if diagnosis is not None else None,
            testcase_url=self.testcase_url,
        )


async def run_autowebcompat_diagnosis(
    config: TaskConfig,
    tracker: RunTracker,
    input_data: AutoWebcompatInput,
    bugzilla_mcp_server: McpServerConfig,
    publish_file: PublishFile,
) -> AutowebcompatDiagnosisResult:
    """Confirm a web-compat issue reproduces, then diagnose why."""
    firefox_browser = FirefoxBrowsers()
    chrome_browser = ChromeBrowsers()

    plan_task = DiagnosisPlan(config, tracker, input_data, bugzilla_mcp_server)
    plan_result = await plan_task.run()

    channel = FirefoxChannel(plan_result.firefox_channel)
    logger.info(
        "Diagnosing on Firefox %s: %s", channel.value, plan_result.channel_rationale
    )
    firefox_path = getattr(firefox_browser, channel.value)
    chrome_path = chrome_browser.stable

    # If the attached script still demonstrates the difference, that settles the
    # reproduction without spending an agent task on it.
    repro_result = None
    if plan_result.script_path is not None:
        repro_result = run_confirmation_script(
            plan_result.script_path, firefox_path, chrome_path
        )
        if repro_result is None:
            logger.info(
                "Attached script did not demonstrate the difference; "
                "falling back to the reproduction task"
            )
    if repro_result is None:
        repro_task = ReproScript(
            config, tracker, firefox_path, chrome_path, plan_result
        )
        repro_result = await repro_task.run()

    results = DiagnosisResults(publish_file, repro_result)

    if not repro_result.reproduced:
        logger.info(
            "Issue did not reproduce (%s); skipping diagnosis",
            repro_result.failure_reason,
        )
        return results.into_result()

    if repro_result.script_path is None:
        logger.info("No validated script; diagnosing from the reproduction steps")

    diagnosis_task = Diagnosis(
        config, tracker, firefox_path, chrome_path, plan_result, repro_result
    )
    results.set_diagnosis(await diagnosis_task.run())

    return results.into_result()
