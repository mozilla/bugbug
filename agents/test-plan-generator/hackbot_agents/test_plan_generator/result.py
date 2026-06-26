"""Structured result reporting for the test-plan-generator agent."""

from __future__ import annotations

from typing import Literal

from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel, Field, ValidationError, model_validator

RESULT_SERVER_NAME = "test-plan-generator"
SUBMIT_RESULT_TOOL = f"mcp__{RESULT_SERVER_NAME}__submit_result"


class GeneratedTestCase(BaseModel):
    id: int = Field(description="Sequential case id starting at 1.")
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
    failure_reason: str | None = Field(
        default=None,
        description=(
            "Required when status is failed. A concise reason why the step failed, "
            "based only on what was observed during execution."
        ),
    )

    @model_validator(mode="after")
    def _validate_failure_reason(self) -> "StepResult":
        if self.status == "failed" and not self.failure_reason:
            raise ValueError("failed steps must include failure_reason")
        return self


class TestCaseResult(BaseModel):
    id: int
    status: Literal["passed", "failed", "unsuitable"]
    step_results: list[StepResult]
    summary: str
    failure_reason: str | None = Field(
        default=None,
        description=(
            "Required when status is failed or unsuitable. A concise reason why "
            "the case failed or could not be run, useful for later developer review."
        ),
    )

    @model_validator(mode="after")
    def _validate_failure_reason(self) -> "TestCaseResult":
        if self.status in {"failed", "unsuitable"} and not self.failure_reason:
            raise ValueError("failed or unsuitable cases must include failure_reason")
        return self


class TestPlanResult(BaseModel):
    feature: str
    generated_test_cases: list[GeneratedTestCase]
    results: list[TestCaseResult]
    summary: str

    @model_validator(mode="after")
    def _validate_result(self) -> "TestPlanResult":
        case_count = len(self.generated_test_cases)
        if not 1 <= case_count <= 20:
            raise ValueError("generated_test_cases must contain 1 to 20 cases")

        case_ids = [case.id for case in self.generated_test_cases]
        expected_ids = list(range(1, case_count + 1))
        if case_ids != expected_ids:
            raise ValueError("generated test case ids must be sequential starting at 1")

        result_ids = [result.id for result in self.results]
        if result_ids != expected_ids:
            raise ValueError(
                "results must contain one result for each generated test case id"
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
        "Call exactly once, after all generated test cases have been run.",
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
