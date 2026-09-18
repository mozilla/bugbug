"""Tests for the agent registry and generic env serialization."""

import json

import pytest
from app.agents import AGENT_REGISTRY, model_to_env
from app.schemas import (
    BugFixInputs,
    BuildRepairInputs,
    TestRepairInputs,
    UpliftInputs,
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


def test_bugzilla_needinfo_flag_id_selects_follow_up_mode():
    inputs = BugFixInputs(
        bug_id=1,
        bugzilla_needinfo_flag_id=2187233,
        comment=(
            "Check whether Bugzilla user user@example.com posted a comment at "
            "exactly 2026-08-21T16:36:29."
        ),
    )
    env = model_to_env(inputs)
    assert env["BUGZILLA_NEEDINFO_FLAG_ID"] == "2187233"
    assert env["COMMENT"] == inputs.comment


def test_bugzilla_needinfo_requires_comment_context():
    with pytest.raises(ValidationError, match="comment"):
        BugFixInputs(bug_id=1, bugzilla_needinfo_flag_id=2187233)


def test_bugzilla_needinfo_rejects_phabricator_context():
    with pytest.raises(ValidationError, match="cannot be combined"):
        BugFixInputs(
            bug_id=1,
            revision_id=42,
            comment="@hackbot please fix",
            bugzilla_needinfo_flag_id=2187233,
        )


def test_bugzilla_needinfo_flag_id_must_be_positive():
    with pytest.raises(ValidationError, match="greater than 0"):
        BugFixInputs(
            bug_id=1,
            bugzilla_needinfo_flag_id=0,
            comment="Needinfo trigger context",
        )


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


def test_test_plan_generator_inputs_require_feature_description():
    with pytest.raises(ValidationError):
        PlanGeneratorInputs(
            feature_name="Bookmarks and History",
            test_scope="Bookmarks toolbar behavior.",
        )


def test_test_plan_generator_env_serialization():
    env = model_to_env(
        PlanGeneratorInputs(
            feature_name="Bookmarks and History",
            feature_description="Bookmarks and history controls in Firefox.",
            test_scope="Bookmarks toolbar behavior.",
        )
    )

    assert env == {
        "FEATURE_NAME": "Bookmarks and History",
        "FEATURE_DESCRIPTION": "Bookmarks and history controls in Firefox.",
        "TEST_SCOPE": "Bookmarks toolbar behavior.",
    }


def test_test_plan_generator_registry_uses_default_env_serializer():
    spec = AGENT_REGISTRY["test-plan-generator"]

    assert spec.build_env is None
    assert spec.job_name == "hackbot-agent-test-plan-generator"
    assert spec.input_schema is PlanGeneratorInputs


def test_test_repair_registry_entry():
    spec = AGENT_REGISTRY["test-repair"]
    assert spec.build_env is None
    assert spec.input_schema is TestRepairInputs
    assert spec.job_name == "hackbot-agent-test-repair"
    assert spec.auto_apply_actions is True


def test_test_repair_env_serialization():
    # The agent resolves the test, commit range and clone depth from the task id,
    # so the only per-run input is the failing task mapping.
    env = model_to_env(
        TestRepairInputs(
            failure_tasks={"test-linux1804-64/opt-xpcshell-1": "abc123"},
        )
    )
    assert json.loads(env["FAILURE_TASKS"]) == {
        "test-linux1804-64/opt-xpcshell-1": "abc123"
    }


def test_test_repair_inputs_require_failure_tasks():
    with pytest.raises(ValidationError):
        TestRepairInputs(model="claude-opus-4-8")


def test_uplift_registry_entry():
    spec = AGENT_REGISTRY["uplift-merge-conflict-resolver"]
    assert spec.build_env is None, (
        "The uplift agent's inputs need no hand-written env serializer."
    )
    assert spec.input_schema is UpliftInputs, (
        "The registry should validate uplift runs against `UpliftInputs`."
    )
    assert spec.job_name == "hackbot-agent-uplift-merge-conflict-resolver", (
        "The job name should match the deployed Cloud Run Job."
    )
    assert spec.auto_apply_actions is False, (
        "The uplift agent records no actions: its output is a patch for review."
    )


def test_uplift_env_serialization():
    env = model_to_env(
        UpliftInputs(
            target_branch="beta",
            sources=[
                {"kind": "git", "commit": "a" * 40},
                {"kind": "phabricator", "revision_id": 12345, "diff_id": 500},
            ],
            bug_id=1846789,
        )
    )

    assert env["TARGET_BRANCH"] == "beta", "`target_branch` should map to its env var."
    assert "TARGET_COMMIT" not in env, (
        "An unpinned target commit should not leak as an empty env var."
    )
    assert env["BUG_ID"] == "1846789", "`bug_id` should map to its env var."
    assert json.loads(env["SOURCES"]) == [
        {"kind": "git", "commit": "a" * 40},
        {"kind": "phabricator", "revision_id": 12345, "diff_id": 500},
    ], "`sources` should be JSON-encoded in order, since it travels as one env var."


def test_uplift_pinned_target_commit_reaches_the_agent():
    env = model_to_env(
        UpliftInputs(
            target_branch="beta",
            target_commit="a" * 40,
            sources=[{"kind": "git", "commit": "b" * 40}],
        )
    )

    assert env["TARGET_COMMIT"] == "a" * 40, (
        "A caller reproducing a specific uplift pins the commit, since the "
        "branch name it sat on moves."
    )


def test_uplift_inputs_keep_a_mixed_stack_discriminated():
    inputs = UpliftInputs(
        target_branch="esr128",
        sources=[
            {"kind": "phabricator", "revision_id": 9},
            {"kind": "git", "commit": "abc123"},
        ],
    )

    assert [source.kind for source in inputs.sources] == ["phabricator", "git"], (
        "`kind` should discriminate the union so one run can mix both kinds."
    )
    assert inputs.sources[0].diff_id is None, (
        "An unpinned `diff_id` should default to `None`, meaning the latest diff."
    )


def test_uplift_inputs_require_at_least_one_source():
    with pytest.raises(ValidationError, match="at least one source"):
        UpliftInputs(target_branch="beta", sources=[])


def test_uplift_inputs_reject_an_unknown_source_kind():
    with pytest.raises(ValidationError):
        UpliftInputs(target_branch="beta", sources=[{"kind": "hg", "rev": "abc"}])
