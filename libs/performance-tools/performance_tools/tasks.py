"""Shared ``Task`` scaffolding for the Firefox performance agents.

This module holds the generic per-task plumbing: the run-tracking dataclasses,
the ``submit_result`` MCP machinery, and the ``Task`` base class itself. It
knows nothing about any specific task's prompt or result schema.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
    create_sdk_mcp_server,
    tool,
)
from hackbot_runtime import AgentError
from hackbot_runtime.claude import Reporter
from pydantic import BaseModel, ValidationError

HERE = Path(__file__).resolve().parent

logger = logging.getLogger(__name__)

RESULT_SERVER_NAME = "performance-tools"
SUBMIT_RESULT_TOOL = f"mcp__{RESULT_SERVER_NAME}__submit_result"

ResultT = TypeVar("ResultT", bound=BaseModel)


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
    """Accumulates per-task turns and cost for the run's summary.

    Start times are keyed by task name rather than held in a single slot,
    because Microbenchmark and ProfileCapture run concurrently under
    asyncio.gather: with one slot, the second start_task clobbered the first and
    one task's turns and cost were dropped silently, understating the cost of
    every reproduced run.
    """

    def __init__(self) -> None:
        self.task_runs: list[TaskRun] = []
        self._started: dict[str, datetime] = {}

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
        self._started[name] = datetime.now()

    def end_task(self, name: str, result_msg: ResultMessage) -> None:
        start_time = self._started.pop(name, None)
        if start_time is None:
            # Record it anyway: a missing start time costs us a duration, but
            # dropping the entry would lose the turns and cost as well.
            logger.warning("Got end_task for %s without a matching start_task", name)
            start_time = datetime.now()
        self.task_runs.append(
            TaskRun(
                name=name,
                start_time=start_time,
                end_time=datetime.now(),
                num_turns=result_msg.num_turns,
                total_cost_usd=result_msg.total_cost_usd,
            )
        )


class ResultCollector(Generic[ResultT]):
    """Holds the result submitted by the agent, if any."""

    def __init__(self, result_cls: type[ResultT]) -> None:
        self._result_cls: type[ResultT] = result_cls
        self.result: ResultT | None = None


def build_result_server(collector: ResultCollector) -> McpServerConfig:
    """Build an in-process MCP server exposing the ``submit_result`` tool."""

    @tool(
        "submit_result",
        "Submit the final result for this task. Call exactly once, at the end, "
        "after the work is complete.",
        {
            **collector._result_cls.model_json_schema(),
            "additionalProperties": False,
        },
    )
    async def submit_result(args: dict) -> dict:
        try:
            collector.result = collector._result_cls.model_validate(args)
        except ValidationError as exc:
            return {
                "content": [{"type": "text", "text": f"Invalid result: {exc}"}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": "Result recorded."}]}

    return create_sdk_mcp_server(name=RESULT_SERVER_NAME, tools=[submit_result])


class Task(ABC, Generic[ResultT]):
    name: str = "unnamed-task"
    result_server_name: str = RESULT_SERVER_NAME
    submit_result_tool: str = SUBMIT_RESULT_TOOL
    result_cls: type[ResultT]

    def __init__(self, task_config: TaskConfig, run_tracker: RunTracker) -> None:
        self.task_config = task_config
        self.run_tracker = run_tracker
        self.allowed_tools = ["Read", "Grep", "Glob", "Bash", self.submit_result_tool]

        self.result_collector = ResultCollector(self.result_cls)
        self.mcp_servers: dict[str, McpServerConfig] = {}

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
            # The SDK's default 1 MiB message buffer is not enough for this
            # agent's tool results, and the reader dies fatally if it
            # overflows: DevTools snapshots of complex pages serialize to JSON
            # that can exceed it, and inlined libraries or base64 payloads make
            # for very large tool results too. Raise it well above that
            # ceiling.
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
                f"{subject} run failed: {result_msg.result or result_msg.subtype}"
            )
        if self.result_collector.result is None:
            raise AgentError(
                f"{subject}: agent finished without submitting a result via "
                "submit_result"
            )
        return self.result_collector.result
