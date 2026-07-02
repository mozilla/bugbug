"""Tests for the agent registry."""

import pytest
from app.agents import AGENT_REGISTRY
from app.schemas import (
    BugFixInputs,
    BuildRepairInputs,
)
from app.schemas import (
    TestPlanGeneratorInputs as PlanGeneratorInputs,
)
from pydantic import ValidationError


def test_bug_fix_registry_entry():
    spec = AGENT_REGISTRY["bug-fix"]
    assert spec.input_schema is BugFixInputs
    assert spec.job_name == "hackbot-agent-bug-fix"


def test_build_repair_registry_entry():
    spec = AGENT_REGISTRY["build-repair"]
    assert spec.input_schema is BuildRepairInputs
    assert spec.job_name == "hackbot-agent-build-repair"


def test_test_plan_generator_inputs_require_feature_description():
    with pytest.raises(ValidationError):
        PlanGeneratorInputs(
            feature_name="Bookmarks and History",
            test_scope="Bookmarks toolbar behavior.",
        )
