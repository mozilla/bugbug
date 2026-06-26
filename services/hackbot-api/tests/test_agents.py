"""Tests for the agent registry and generic env serialization."""

import pytest
from app.agents import AGENT_REGISTRY, model_to_env
from app.schemas import (
    BugFixInputs,
)
from app.schemas import (
    TestPlanGeneratorInputs as PlanGeneratorInputs,
)
from pydantic import ValidationError


def test_model_to_env_uppercases_and_stringifies():
    env = model_to_env(BugFixInputs(bug_id=12345, model="claude-opus", max_turns=8))
    assert env["BUG_ID"] == "12345"
    assert env["MODEL"] == "claude-opus"
    assert env["MAX_TURNS"] == "8"


def test_model_to_env_skips_none_fields():
    env = model_to_env(BugFixInputs(bug_id=1))
    assert env == {"BUG_ID": "1"}
    # Optional fields left unset must not leak as empty/"None" env vars.
    assert "MODEL" not in env
    assert "EFFORT" not in env


def test_model_to_env_does_not_emit_deploy_constants():
    # The broker loopback URL is static Job config, not a per-run input.
    env = model_to_env(BugFixInputs(bug_id=1, model="x", max_turns=2, effort="high"))
    assert "BUGZILLA_MCP_URL" not in env


def test_bug_fix_registry_uses_default_env_serializer():
    spec = AGENT_REGISTRY["bug-fix"]
    # No hand-written build_env: the router falls back to model_to_env.
    assert spec.build_env is None
    assert spec.input_schema is BugFixInputs


def test_test_plan_generator_inputs_require_feature_details():
    with pytest.raises(ValidationError):
        PlanGeneratorInputs(feature="Bookmarks and History")


def test_test_plan_generator_env_serialization():
    env = model_to_env(
        PlanGeneratorInputs(
            feature="Bookmarks and History",
            feature_details="Bookmarks toolbar behavior",
        )
    )

    assert env == {
        "FEATURE": "Bookmarks and History",
        "FEATURE_DETAILS": "Bookmarks toolbar behavior",
    }


def test_test_plan_generator_registry_uses_default_env_serializer():
    spec = AGENT_REGISTRY["test-plan-generator"]

    assert spec.build_env is None
    assert spec.job_name == "hackbot-agent-test-plan-generator"
    assert spec.input_schema is PlanGeneratorInputs
