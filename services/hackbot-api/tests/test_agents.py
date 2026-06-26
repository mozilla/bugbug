"""Tests for the agent registry and generic env serialization."""

import json

from app.agents import AGENT_REGISTRY, model_to_env
from app.schemas import BugFixInputs, BuildRepairInputs


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


def test_build_repair_registry_entry():
    spec = AGENT_REGISTRY["build-repair"]
    assert spec.build_env is None
    assert spec.input_schema is BuildRepairInputs
    assert spec.job_name == "hackbot-agent-build-repair"


def test_model_to_env_json_encodes_failure_tasks_and_bool():
    tasks = {"build-linux64/opt": "OyF95j0oQ-CF_YuBM1b7vg"}
    env = model_to_env(
        BuildRepairInputs(
            bug_id=1, git_commit="deadbeef", failure_tasks=tasks, run_try_push=True
        )
    )
    assert env["GIT_COMMIT"] == "deadbeef"
    assert json.loads(env["FAILURE_TASKS"]) == tasks
    assert env["RUN_TRY_PUSH"] == "True"
