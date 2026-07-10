"""Firefox web-compatibility reproduction agent.

Drives an agent that reproduces a broken-site report in Firefox
using the Firefox DevTools MCP. The bug is passed either inline as ``bug_data``
text or a Bugzilla ``bug_id`` (read via Bugzilla broker).
"""

from __future__ import annotations

import logging
import os
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

from .browser import FirefoxBrowsers
from .config import BUGZILLA_READ_TOOLS, DEVTOOLS_TOOLS
from .devtools_mcp import build_devtools_server
from .result import (
    RESULT_SERVER_NAME,
    SUBMIT_RESULT_TOOL,
    BugReproductionResult,
    ChromeMaskResult,
    ReproductionResult,
    ResultCollector,
    ResultT,
    TestPlanResult,
    build_result_server,
)
from .setup_profile import setup_profile

HERE = Path(__file__).resolve().parent

logger = logging.getLogger("autowebcompat-repro")


PublishFile = Callable[[str, Path, str | None], str]


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


class AutowebcompatReproResult(BaseModel):
    reproduced: bool
    summary: str
    failure_reason: str | None
    steps: str
    screenshot_url: str | None
    plan_result: TestPlanResult
    reproductions: list[tuple[str, BugReproductionResult | ReproductionResult]]
    chrome_mask_fixed: bool | None


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
        self.allowed_tools = ["Read", "Grep", "Glob", "Bash", self.submit_result_tool]

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
                f"{subject} investigation failed: {result_msg.result or result_msg.subtype}"
            )
        if self.result_collector.result is None:
            raise AgentError(
                f"{subject}: agent finished without submitting a result via submit_result"
            )
        return self.result_collector.result


def make_empty_temp_file(dir: Path, prefix: str | None, suffix: str) -> Path:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=dir)
    f = os.fdopen(fd)
    f.close()
    return Path(path)


class TestPlan(Task):
    name = "test_plan"
    result_cls = TestPlanResult

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        input_data: AutoWebcompatInput,
        bugzilla_mcp_server: McpServerConfig,
    ):
        super().__init__(task_config, run_tracker)
        self.input_data = input_data
        if self.input_data.type == "bug_id":
            self.add_mcp_server("bugzilla", bugzilla_mcp_server, BUGZILLA_READ_TOOLS)

    def subject(self) -> Any:
        return self.input_data.subject()

    def system_prompt(self) -> str:
        return (
            super()
            .system_prompt()
            .format(
                task_details="""
1. Identify the affected URL and the described broken behavior.

2. If the report appears to describe something other than a webcompat issue i.e.
it doesn't meet the criteria under "Definition of a webcompat issue",
submit your findings via `submit_result` with `is_webcompat` set to `false`.

3. Based on the report text determine which versions of Firefox are
likely to be affected by the issue. In particular:
 - Is the issue described as affecting iOS? If so it is unlikely to affect other platforms.
 - If the issue is not iOS, does it appear to affect desktop and Android, or only one or the other.
 - If it affects desktop, is there evidence that the issue is specific to particular operating systems?
   Note that often issues may only be reported on one operating system, but actually affect others.
   An issue can only be assumed to be specific to a particular desktop operating system if it is stated
   that it didn't reproduce on other platforms/
 - Is the issue marked as affecting nightly builds, stable builds, or ESR builds

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


class BugReproduction(Task):
    name = "bug_reproduction"
    result_cls = BugReproductionResult

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        firefox_path: Path,
        profile_path: Path,
        input_data: AutoWebcompatInput,
        bugzilla_mcp_server: McpServerConfig,
        screenshot_dir: Path,
    ):
        super().__init__(task_config, run_tracker)
        self.input_data = input_data
        self.screenshot_path = make_empty_temp_file(
            screenshot_dir, "reproduction=", ".png"
        )
        self.add_mcp_server(
            "firefox-devtools",
            build_devtools_server(
                firefox_path=firefox_path,
                headless=True,
                enable_script=True,
                enable_privileged_context=False,
                profile_path=profile_path,
            ),
            DEVTOOLS_TOOLS,
        )
        if self.input_data.type == "bug_id" != None:
            self.add_mcp_server("bugzilla", bugzilla_mcp_server, BUGZILLA_READ_TOOLS)

    def subject(self) -> Any:
        return self.input_data.subject()

    def system_prompt(self) -> str:
        return (
            super()
            .system_prompt()
            .format(
                task_details=f"""
1. Identify the affected URL and the described broken behavior.
2. Baseline: Navigate to the URL with the Firefox DevTools MCP and
   try to reproduce the described broken behaviour.
3. If the issue reproduces AND the breakage is visual in nature (incorrect
   layout or rendering, not broken interaction), capture a screenshot showing
   it: call `screenshot_page` with `saveTo` set to `{self.screenshot_path}`.
   This writes the image to that file instead of returning it — do not capture
   or paste the image data yourself. Then set `screenshot_path` in your result
   to exactly `{self.screenshot_path}`. For non-visual issues, take no
   screenshot and leave `screenshot_path` null.
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


class StepsReproduction(Task):
    name = "steps_reproduction"
    result_cls = ReproductionResult

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        firefox_path: Path,
        profile_path: Path,
        steps: str,
    ):
        super().__init__(task_config, run_tracker)
        self.steps = steps
        self.add_mcp_server(
            "firefox_devtools",
            build_devtools_server(
                firefox_path=firefox_path,
                headless=True,
                enable_script=True,
                enable_privileged_context=False,
                profile_path=profile_path,
            ),
            DEVTOOLS_TOOLS,
        )

    def subject(self) -> Any:
        return self.steps

    def system_prompt(self) -> str:
        return (
            super()
            .system_prompt()
            .format(
                task_details="""
1. Run the reproduction steps
2. Submit your findings via `submit_result` (see "Reporting your result").
"""
            )
        )

    def user_prompt(self) -> str:
        return f"""Here are the steps to reproduce the issue:
{self.steps}"""


class ChromeMaskReproduction(Task):
    name = "chrome_mask"
    result_cls = ChromeMaskResult

    def __init__(
        self,
        task_config: TaskConfig,
        run_tracker: RunTracker,
        firefox_path: Path,
        profile_path: Path,
        steps: str,
    ) -> None:
        super().__init__(task_config, run_tracker)
        self.steps = steps
        self.add_mcp_server(
            "firefox_devtools",
            build_devtools_server(
                firefox_path=firefox_path,
                headless=True,
                enable_script=True,
                enable_privileged_context=True,
                profile_path=profile_path,
            ),
            DEVTOOLS_TOOLS,
        )

    def subject(self) -> Any:
        return self.steps

    def system_prompt(self) -> str:
        return (
            super()
            .system_prompt()
            .format(
                task_details="""
1. Identify the affected URL from the reproduction steps.
2.  **Enable Chrome Mask for the site**:
   - Call `list_extensions` and read Chrome Mask's **UUID** field. Build its
     options URL as `moz-extension://<UUID>/options.html` and `navigate_page` to it.
   - Add the **bare hostname** of the affected URL (e.g. `example.com`, no
     scheme/path) via the "Add Site" form (`take_snapshot`, then `fill_by_uid` /
     `click_by_uid`), and submit. Confirm it appears under "Currently Masked Sites".
3. **Confirm the mask is active:**
   - Switch back to the affected tab and do a page reload.
   - Run `evaluate_script: () => navigator.userAgent` — it **must contain `Chrome`**.
     Judge activeness only from the UA string, not from page appearance. If it
     still reads Firefox, recheck step 2 and reload.
4. Run the reproduction steps
5. Submit your findings via `submit_result` (see "Reporting your result").
"""
            )
        )

    def user_prompt(self) -> str:
        return f"""Here are the steps to reproduce the issue:
{self.steps}"""


class FirefoxChannel(Enum):
    nightly = "nightly"
    stable = "stable"
    esr = "esr"


@dataclass
class InitialReproduction:
    channel: FirefoxChannel
    steps: str
    summary: str
    screenshot_path: Path | None


class ReproductionResults:
    def __init__(self, publish_file: PublishFile, plan_result: TestPlanResult):
        self.plan_result = plan_result
        self.publish_file = publish_file
        self.results: dict[
            tuple[FirefoxChannel, str | None], ReproductionResult | ChromeMaskResult
        ] = {}
        self.initial_repro: InitialReproduction | None = None
        self.chrome_mask_fixed: bool | None = None

    @property
    def reproduced(self) -> bool:
        return self.initial_repro is not None

    @property
    def summary(self) -> str:
        return self.initial_repro.summary if self.initial_repro is not None else ""

    @property
    def failure_reason(self) -> str | None:
        if self.reproduced:
            return None
        for result in self.results.values():
            # Return the first failure reason we got
            if (
                isinstance(result, ReproductionResult)
                and result.failure_reason is not None
            ):
                return result.failure_reason
        return None

    @property
    def steps(self) -> str:
        return self.initial_repro.steps if self.initial_repro is not None else ""

    @property
    def screenshot_url(self) -> str | None:
        if (
            self.initial_repro is not None
            and self.initial_repro.screenshot_path is not None
        ):
            return self.publish_file(
                f"screenshot-{self.initial_repro.channel}.png",
                self.initial_repro.screenshot_path,
                "image/png",
            )

        return None

    def set_result(
        self,
        channel: FirefoxChannel,
        extra: str | None,
        result: ReproductionResult | ChromeMaskResult,
    ) -> None:
        key = (channel, extra)
        if key in self.results:
            raise ValueError(f"Got duplicate results for {channel}, {extra}")
        if isinstance(result, BugReproductionResult) and result.reproduced:
            if self.initial_repro is not None:
                raise ValueError("Got duplicate steps / summary")
            self.initial_repro = InitialReproduction(
                channel, result.steps, result.summary, result.screenshot_path
            )
        elif isinstance(result, ChromeMaskResult):
            if self.chrome_mask_fixed is not None:
                raise ValueError("Got duplicate results for chrome mask")
            self.chrome_mask_fixed = result.chrome_mask_fixed

        self.results[key] = result

    def into_result(self) -> AutowebcompatReproResult:
        return AutowebcompatReproResult(
            reproduced=self.reproduced,
            summary=self.summary,
            failure_reason=self.failure_reason,
            steps=self.steps,
            screenshot_url=self.screenshot_url,
            plan_result=self.plan_result,
            reproductions=[
                (key[0].value, value.model_copy(update={"screenshot_path": None}))
                for key, value in self.results.items()
                if isinstance(value, ReproductionResult)
            ],
            chrome_mask_fixed=self.chrome_mask_fixed,
        )


async def run_autowebcompat_repro(
    default_config: TaskConfig,
    tracker: RunTracker,
    input_data: AutoWebcompatInput,
    bugzilla_mcp_server: McpServerConfig,
    publish_file: PublishFile,
) -> AutowebcompatReproResult:
    """Reproduce a web-compat issue and return the agent's findings.

    Returns a :class:`AutowebcompatReproResult` on success; raises
    :class:`AgentError` if the agent ends in an error.
    """
    firefox_browser = FirefoxBrowsers()

    test_plan_task = TestPlan(default_config, tracker, input_data, bugzilla_mcp_server)
    test_plan_result = await test_plan_task.run()
    repro_results = ReproductionResults(publish_file, test_plan_result)

    if not test_plan_result.is_webcompat:
        result = repro_results.into_result()
        result.summary = "Test was identified as a non-compat issue"
        result.failure_reason = "non_compat"
        return result
    elif test_plan_result.affects_platforms == ["ios"]:
        result = repro_results.into_result()
        result.summary = "Issue was identified as iOS only"
        result.failure_reason = "unsupported_platform"
        return result

    async def next_repro_task(
        channel: FirefoxChannel,
        extra: str | None = None,
        config: TaskConfig = default_config,
    ) -> None:
        browser = getattr(firefox_browser, channel.value)
        profile = setup_profile(browser)
        if repro_results.initial_repro is None:
            task: Task = BugReproduction(
                config,
                tracker,
                browser,
                profile,
                input_data,
                bugzilla_mcp_server,
                screenshots_dir,
            )
        else:
            task = StepsReproduction(
                config,
                tracker,
                browser,
                profile,
                repro_results.initial_repro.steps,
            )
        logger.info(
            "Trying reproduction in %s%s",
            channel,
            f" {extra}" if extra is not None else "",
        )
        repro_results.set_result(channel, extra, await task.run())

    screenshots_dir = Path(tempfile.mkdtemp(prefix="autowebcompat-screenshots-"))

    # Always try in nightly first
    await next_repro_task(FirefoxChannel.nightly)

    if not repro_results.reproduced and test_plan_result.affects_platforms == [
        "android"
    ]:
        result = repro_results.into_result()
        result.summary = "Issue was identified as Android only and didn't reproduce on desktop nightly"
        result.failure_reason = "unsupported_platform"
        return result

    # If we don't think this is ESR only, try stable
    if (
        "stable" in test_plan_result.affects_channels
        or "nightly" in test_plan_result.affects_channels
    ):
        await next_repro_task(FirefoxChannel.stable)

    if repro_results.reproduced or "esr" in test_plan_result.affects_channels:
        # If we have any result try ESR as a possible regression baseline,
        # otherwise try ESR if we think it's affected
        await next_repro_task(FirefoxChannel.esr)

    if repro_results.initial_repro is not None:
        channel = repro_results.initial_repro.channel
        browser = getattr(firefox_browser, channel.value)
        profile = setup_profile(browser, extensions=["chrome-mask"])

        task = ChromeMaskReproduction(
            default_config,
            tracker,
            browser,
            profile,
            repro_results.initial_repro.steps,
        )
        repro_results.set_result(channel, "chrome-mask", await task.run())

    return repro_results.into_result()
