"""Structured result reporting for the test-plan-generator agent."""

from __future__ import annotations

from typing import Literal

from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel, Field, ValidationError, model_validator

RESULT_SERVER_NAME = "test-plan-generator"
SUBMIT_RESULT_TOOL = f"mcp__{RESULT_SERVER_NAME}__submit_result"


class GeneratedTestCase(BaseModel):
    id: int = Field(description="Sequential case id from 1 through 10.")
    title: str
    context: Literal["chrome", "content"]
    preconditions: str | None = None
    steps: list[str] = Field(
        description="Concise test steps for this case; between 1 and 6 steps."
    )

    @model_validator(mode="after")
    def _validate_steps(self) -> "GeneratedTestCase":
        if not 1 <= len(self.steps) <= 6:
            raise ValueError("each generated test case must have 1 to 6 steps")
        return self


class StepResult(BaseModel):
    step_number: int
    status: Literal["passed", "failed", "not_run"]
    observation: str


class TestCaseResult(BaseModel):
    id: int
    status: Literal["passed", "failed", "unsuitable"]
    step_results: list[StepResult]
    summary: str


class TestPlanResult(BaseModel):
    feature: str
    generated_test_cases: list[GeneratedTestCase]
    results: list[TestCaseResult]
    summary: str

    @model_validator(mode="after")
    def _validate_result(self) -> "TestPlanResult":
        if len(self.generated_test_cases) != 10:
            raise ValueError("generated_test_cases must contain exactly 10 cases")

        case_ids = [case.id for case in self.generated_test_cases]
        if case_ids != list(range(1, 11)):
            raise ValueError("generated test case ids must be 1 through 10")

        result_ids = [result.id for result in self.results]
        if result_ids != list(range(1, 11)):
            raise ValueError(
                "results must contain one result for each case id 1 through 10"
            )

        return self


SUBMIT_RESULT_SCHEMA = {
    **TestPlanResult.model_json_schema(),
    "additionalProperties": False,
}


class ResultCollector:
    """Holds the result submitted by the agent, if any."""

    def __init__(self) -> None:
        self.result: TestPlanResult | None = None


def build_result_server(collector: ResultCollector) -> McpServerConfig:
    """Build an in-process MCP server exposing the ``submit_result`` tool."""

    @tool(
        "submit_result",
        "Submit the final generated Firefox QA test plan and execution result. "
        "Call exactly once, after all 10 test cases have been generated and run.",
        SUBMIT_RESULT_SCHEMA,
    )
    async def submit_result(args: dict) -> dict:
        try:
            collector.result = TestPlanResult.model_validate(args)
        except ValidationError as exc:
            return {
                "content": [{"type": "text", "text": f"Invalid result: {exc}"}],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": "Result recorded."}]}

    return create_sdk_mcp_server(name=RESULT_SERVER_NAME, tools=[submit_result])
