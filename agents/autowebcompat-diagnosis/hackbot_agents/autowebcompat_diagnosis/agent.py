"""Firefox web-compatibility diagnosis agent."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from autowebcompat_tools.environment import Environment
from autowebcompat_tools.inputs import AutoWebcompatInput, BugDataInput, BugIdInput
from autowebcompat_tools.result import ResultT
from autowebcompat_tools.task import RunTracker, TaskConfig
from autowebcompat_tools.task import Task as BaseTask
from claude_agent_sdk import McpServerConfig
from pydantic import BaseModel

from .browser import ChromeBrowsers, FirefoxBrowsers
from .config import BUGZILLA_READ_TOOLS, CHROME_DEVTOOLS_TOOLS, DEVTOOLS_TOOLS
from .mcp_servers import build_chrome_devtools_server, build_firefox_devtools_server
from .result import (
    RESULT_SERVER_NAME,
    DiagnosisPlanResult,
    DiagnosisResult,
    DiagnosisText,
    ReproScriptResult,
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


class AutowebcompatDiagnosisResult(BaseModel):
    reproduced: bool
    failure_reason: str | None
    root_cause: str | None
    evidence: str | None
    testcase_url: str | None


class Task(BaseTask[ResultT]):
    result_server_name = RESULT_SERVER_NAME

    def __init__(self, task_config: TaskConfig, run_tracker: RunTracker):
        super().__init__(task_config, run_tracker)
        self.allowed_tools.insert(1, "Write")

    def system_prompt(self) -> str:
        return (HERE / "prompts" / "system.md").read_text()


def run_script(
    script_path: Path, browser: str, browser_path: Path, headless: bool
) -> int | None:
    """Run the reproduction script in one browser; return its exit code.

    Returns ``None`` if the script timed out, i.e. gave no verdict.
    """
    script_timeout = 5 * 60
    env = {
        **os.environ,
        "NODE_PATH": str(NODE_MODULES),
        "BROWSER": browser,
        "BROWSER_BIN": str(browser_path),
    }
    if headless:
        env["HEADLESS"] = "1"

    try:
        proc = subprocess.run(
            ["node", str(script_path)],
            env=env,
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
    script_path: Path, firefox_path: Path, chrome_path: Path, headless: bool
) -> ReproScriptResult | None:
    """Check the script still demonstrates the difference, without an agent.

    The difference is demonstrated when the Firefox run exits 1 (not working)
    and the Chrome run exits 0 (working). Returns ``None`` for any other
    outcome — wrong exit codes, a script error, or a timeout — so the caller
    can fall back to the agent task.
    """
    firefox_code = run_script(script_path, "firefox", firefox_path, headless)
    if firefox_code != 1:
        return None
    chrome_code = run_script(script_path, "chrome", chrome_path, headless)
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
                headless=task_config.headless,
                enable_script=True,
                enable_privileged_context=False,
            ),
            DEVTOOLS_TOOLS,
        )
        self.add_mcp_server(
            "chrome-devtools",
            build_chrome_devtools_server(
                chrome_path=chrome_path, headless=task_config.headless
            ),
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
   site in Firefox with the Firefox DevTools MCP, then run the same steps
   in Chrome with the Chrome DevTools
   MCP.
   - A genuine web-compat issue reproduces in Firefox but not in Chrome. If the
     behavior is identical in both, your steps may be wrong; refine the steps
     and re-check before concluding.
   - If you confirmed that the reported broken behaviour reproduces in both
     browsers, set `failure_reason` to `not_firefox_specific`.
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
        self.diagnosis_path = self.work_dir / "diagnosis.json"
        self.add_mcp_server(
            "firefox-devtools",
            build_firefox_devtools_server(
                firefox_path=firefox_path,
                headless=task_config.headless,
                enable_script=True,
                enable_privileged_context=False,
            ),
            DEVTOOLS_TOOLS,
        )
        self.add_mcp_server(
            "chrome-devtools",
            build_chrome_devtools_server(
                chrome_path=chrome_path, headless=task_config.headless
            ),
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

5. Write your diagnosis to exactly {self.diagnosis_path} as a JSON object
   matching this schema:
{json.dumps(DiagnosisText.model_json_schema(), indent=2)}

6. Submit your diagnosis via `submit_result` (see "Reporting your result").
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
    def diagnosis(self) -> DiagnosisText | None:
        if self.diagnosis_result is None:
            return None
        return self.diagnosis_result.diagnosis

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
        diagnosis = self.diagnosis
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
    with Environment() as env:
        if not config.headless:
            env.start_xvfb()

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
                plan_result.script_path, firefox_path, chrome_path, config.headless
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
