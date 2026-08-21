"""TestRail recordable actions."""

from __future__ import annotations

from typing import Annotated, Any

from agent_tools.registry import ToolError, tool, tools_in
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from hackbot_runtime.actions.recorder import ActionsRecorder

ACTION_TYPE = "testrail.submit_test_plan"


class TestRailStepInput(BaseModel):
    action: str = Field(description="Test step action.")
    expectation: str | None = Field(
        default=None,
        description=("Expected result for this step."),
    )


class TestRailCaseInput(BaseModel):
    id: int
    title: str = Field(description="TestRail test case title.")
    preconditions: str | None = Field(
        default=None, description="Optional setup required before running this case."
    )
    steps: list[TestRailStepInput] = Field(
        min_length=1,
        description=(
            "Ordered steps a QA engineer should follow. Each step has an action "
            "and an optional expectation."
        ),
    )

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("steps")
    @classmethod
    def steps_must_not_be_blank(
        cls, value: list[TestRailStepInput]
    ) -> list[TestRailStepInput]:
        if any(not step.action.strip() for step in value):
            raise ValueError("step's actions must not contain blank items")
        return value

    @model_validator(mode="after")
    def expectations_must_include_verification(self) -> "TestRailCaseInput":
        if not any(step.expectation for step in self.steps):
            raise ValueError("at least one step must include an expected result")
        return self


class SubmitTestPlanInput(BaseModel):
    feature: str = Field(description="Feature covered by the generated test cases.")
    generated_test_cases: list[TestRailCaseInput] = Field(
        min_length=1,
        max_length=30,
        description="Generated test cases to upload to TestRail.",
    )

    @field_validator("feature")
    @classmethod
    def feature_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("feature must not be blank")
        return value

    @model_validator(mode="after")
    def case_ids_must_be_sequential(self) -> "SubmitTestPlanInput":
        case_ids = [case.id for case in self.generated_test_cases]
        expected_ids = list(range(1, len(self.generated_test_cases) + 1))
        if case_ids != expected_ids:
            raise ToolError("test case ids must be sequential starting at 1")
        return self


def _confirm(action: dict) -> str:
    return f"Recorded {action['type']} (ID: {action['action_id']})."


def _validated_params(feature: str, generated_test_cases: list[Any]) -> dict[str, Any]:
    try:
        validated = SubmitTestPlanInput.model_validate(
            {"feature": feature, "generated_test_cases": generated_test_cases}
        )
    except ValidationError as exc:
        raise ToolError(
            "invalid TestRail submission",
            payload={"error": "invalid TestRail submission", "details": exc.errors()},
        ) from exc
    return validated.model_dump(mode="json")


@tool
async def submit_test_plan(
    recorder: ActionsRecorder,
    feature: Annotated[
        str,
        Field(
            description=(
                "Feature name for the new TestRail suite. The apply step always "
                "creates a new suite for this feature."
            )
        ),
    ],
    generated_test_cases: Annotated[
        list[TestRailCaseInput],
        # Bounds are repeated from SubmitTestPlanInput: this copy is the agent's
        # schema, that one is the enforcement.
        Field(
            min_length=1,
            max_length=30,
            description="Generated test cases to upload together to TestRail.",
        ),
    ],
) -> str:
    """Record a generated test plan for deferred TestRail submission.

    Call this at most once per run: the apply step creates a new TestRail suite
    for every recorded action, so a second call would duplicate the whole plan.
    The apply step creates the suite, creates a section in it, and uploads all
    supplied test cases. Nothing is sent to TestRail during the agent run.
    """
    if any(action["type"] == ACTION_TYPE for action in recorder.actions):
        raise ToolError(
            "a test plan is already recorded for this run; do not call "
            "submit_test_plan again"
        )
    params = _validated_params(feature, generated_test_cases)
    action = recorder.record(ACTION_TYPE, params)
    return _confirm(action)


TOOLS = tools_in(__name__)
