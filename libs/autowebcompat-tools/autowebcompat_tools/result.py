"""The ``submit_result`` tool an agent calls to hand back its validated result."""

from __future__ import annotations

from typing import Generic, TypeVar

from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel, ValidationError

ResultT = TypeVar("ResultT", bound=BaseModel)


class ResultCollector(Generic[ResultT]):
    """Holds the result submitted by the agent, if any."""

    def __init__(self, result_cls: type[ResultT]) -> None:
        self.result_cls: type[ResultT] = result_cls
        self.result: ResultT | None = None


def submit_result_tool_name(server_name: str) -> str:
    """The ``submit_result`` tool's name as the agent sees it."""
    return f"mcp__{server_name}__submit_result"


def build_result_server(
    server_name: str, collector: ResultCollector
) -> McpServerConfig:
    """Build an in-process MCP server exposing the ``submit_result`` tool.

    The handler validates the payload against the collector's result class and
    stores it. A validation error is returned to the model (as tool output) so
    it can correct and resubmit rather than failing the run.
    """

    @tool(
        "submit_result",
        "Submit the final result for this task. Call exactly once, at the end, "
        "after completing the task.",
        {
            **collector.result_cls.model_json_schema(),
            "additionalProperties": False,
        },
    )
    async def submit_result(args: dict) -> dict:
        try:
            collector.result = collector.result_cls.model_validate(args)
        except ValidationError as exc:
            return {
                "content": [{"type": "text", "text": f"Invalid result: {exc}"}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": "Result recorded."}]}

    return create_sdk_mcp_server(name=server_name, tools=[submit_result])
