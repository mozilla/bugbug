from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    timed_out = "timed_out"


class ArtifactRef(BaseModel):
    name: str
    size: int
    content_type: str | None = None


class RunSummary(BaseModel):
    status: str
    error: str | None = None
    findings: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)


class RunActionDoc(BaseModel):
    """A recorded action and its apply state, as shown/driven by the UI."""

    model_config = ConfigDict(from_attributes=True)

    idx: int
    type: str
    params: dict[str, Any]
    ref: str | None = None
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    applied_at: datetime | None = None


class AgentDescriptor(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class RunRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    agent: str
    status: RunStatus


class RunDoc(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    agent: str
    status: RunStatus
    inputs: dict[str, Any]
    requested_by: str | None = None
    created_at: datetime
    updated_at: datetime
    execution_name: str | None = None
    results_prefix: str
    summary: RunSummary | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    error: str | None = None


# --- Per-agent input schemas ---


class BugFixInputs(BaseModel):
    bug_id: int
    # When following up on an existing Phabricator revision (e.g. triggered by a
    # webhook), the revision to update and the comment that mentioned Hackbot.
    # Both are omitted for a plain "fix this bug" run and for Bugzilla needinfo.
    revision_id: int | None = None
    comment: str | None = None
    # Set only by a Bugzilla flag.needinfo webhook. It selects the dedicated
    # follow-up mode, whose first step is fetching the bug and its comments.
    bugzilla_needinfo: bool | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    @model_validator(mode="after")
    def _validate_mode(self) -> "BugFixInputs":
        """Require exactly one coherent normal, Phabricator, or Bugzilla mode."""
        if self.bugzilla_needinfo:
            if self.revision_id is not None or self.comment is not None:
                raise ValueError(
                    "bugzilla_needinfo cannot be combined with revision_id or comment"
                )
        elif self.revision_id is not None:
            if not self.comment:
                raise ValueError("comment is required when revision_id is set")
        elif self.comment is not None:
            raise ValueError("comment requires revision_id")
        return self


class AutowebcompatReproInputs(BaseModel):
    bug_data: str | None = None
    bug_id: int | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    @model_validator(mode="after")
    def _require_subject(self) -> "AutowebcompatReproInputs":
        if self.bug_data is None and self.bug_id is None:
            raise ValueError("provide at least one of bug_data or bug_id")
        return self


class BuildRepairInputs(BaseModel):
    # Failing Taskcluster build tasks {task_name: task_id}; the agent resolves the
    # push commits from them. git_commit / bug_id are optional overrides.
    failure_tasks: dict[str, str]
    git_commit: str | None = None
    bug_id: int | None = None
    run_try_push: bool = False
    model: str | None = None
    max_turns: int | None = None


class TestRepairInputs(BaseModel):
    # Failing Taskcluster test tasks {task_name: task_id}. The agent resolves the
    # push, the last-green revision and the candidate commit range itself from the
    # task id (the listener only filters which failures are worth investigating).
    failure_tasks: dict[str, str]
    model: str | None = None
    max_turns: int | None = None


class FrontendTriageInputs(BaseModel):
    bug_id: int
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None


class TestPlanGeneratorInputs(BaseModel):
    feature_name: str
    feature_description: str
    test_scope: str
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None
