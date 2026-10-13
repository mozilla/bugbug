"""One Claude session per task, ending in a validated result."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generic

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    EffortLevel,
    McpServerConfig,
    ResultMessage,
)
from claude_agent_sdk.types import SystemPromptPreset
from hackbot_runtime import AgentError
from hackbot_runtime.claude import Reporter
from pydantic import ValidationError

from autowebcompat_tools.result import (
    ResultCollector,
    ResultT,
    build_result_server,
    submit_result_tool_name,
)

logger = logging.getLogger(__name__)


@dataclass
class TaskConfig:
    model: str | None = None
    max_turns: int | None = None
    effort: EffortLevel | None = None
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


async def last_result(
    client: ClaudeSDKClient, reporter: Reporter, subject: Any
) -> ResultMessage:
    """Drain the response to the last query and return its ResultMessage."""
    result_msg: ResultMessage | None = None
    async for msg in client.receive_response():
        reporter.message(msg)
        if isinstance(msg, ResultMessage):
            result_msg = msg
    if result_msg is None:
        raise AgentError(f"{subject}: agent produced no result message")
    return result_msg


class Task(ABC, Generic[ResultT]):
    """One Claude session, ending in a validated ``submit_result`` payload.

    The system prompt is Claude Code's own unless the agent overrides
    ``system_prompt()``: a preset with an ``append``, or a full replacement
    string. The task itself goes in the user prompt. Each agent sets its own
    ``result_server_name`` for the ``submit_result`` tool.
    """

    name: str = "unnamed-task"
    result_cls: type[ResultT]
    result_server_name: str

    def __init__(self, task_config: TaskConfig, run_tracker: RunTracker):
        self.task_config = task_config
        self.run_tracker = run_tracker
        self.result_collector = ResultCollector(self.result_cls)
        self.allowed_tools: list[str] = ["Read", "Grep", "Glob", "Bash"]
        self.mcp_servers: dict[str, McpServerConfig] = {}
        result_server = self.result_server()
        if result_server is not None:
            self.add_mcp_server(
                self.result_server_name,
                result_server,
                [submit_result_tool_name(self.result_server_name)],
            )

    def add_mcp_server(
        self, name: str, server: McpServerConfig, tools: list[str]
    ) -> None:
        self.mcp_servers[name] = server
        self.allowed_tools.extend(tools)

    def result_server(self) -> McpServerConfig | None:
        return build_result_server(self.result_server_name, self.result_collector)

    def system_prompt(self) -> str | SystemPromptPreset:
        return {"type": "preset", "preset": "claude_code"}

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

        with Reporter(
            verbose=self.task_config.verbose, log_path=self.task_config.log
        ) as reporter:
            reporter.header(subject)

            async with ClaudeSDKClient(options=self.agent_options()) as client:
                await client.query(self.user_prompt())
                result_msg = await last_result(client, reporter, subject)

        self.run_tracker.end_task(self.name, result_msg)
        if result_msg.is_error:
            raise AgentError(
                f"{subject}: {self.name} failed: "
                f"{result_msg.result or result_msg.subtype}"
            )
        if self.result_collector.result is None:
            raise AgentError(
                f"{subject}: agent finished without submitting a result via submit_result"
            )
        return self.result_collector.result


class StructuredTask(Task[ResultT]):
    """A Task whose result is the session's structured output.

    The CLI retries output that doesn't match the JSON schema; a result that then
    fails the Pydantic validators gets one follow-up asking for a fix.
    """

    def result_server(self) -> McpServerConfig | None:
        return None

    def agent_options(self) -> ClaudeAgentOptions:
        options = super().agent_options()
        options.output_format = {
            "type": "json_schema",
            "schema": self.result_cls.model_json_schema(),
        }
        return options

    async def run(self) -> ResultT:
        self.run_tracker.start_task(self.name)
        subject = self.subject()
        logger.info("Running %s with %s", self.__class__.__name__, str(subject)[:200])

        with Reporter(
            verbose=self.task_config.verbose, log_path=self.task_config.log
        ) as reporter:
            reporter.header(subject)
            async with ClaudeSDKClient(options=self.agent_options()) as client:
                await client.query(self.user_prompt())
                msg = await last_result(client, reporter, subject)
                try:
                    result = self.result_cls.model_validate(msg.structured_output)
                except ValidationError as exc:
                    if msg.is_error:
                        raise AgentError(
                            f"{subject}: {self.name} failed: "
                            f"{msg.result or msg.subtype}"
                        ) from exc
                    await client.query(f"Invalid result: {exc}. Fix it and resubmit.")
                    msg = await last_result(client, reporter, subject)
                    result = self.result_cls.model_validate(msg.structured_output)

        self.run_tracker.end_task(self.name, msg)
        return result
