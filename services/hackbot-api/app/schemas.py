from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union
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
    findings: dict[str, Any] = {}
    actions: list[dict[str, Any]] = []


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
    """The API's answer to "start this run": which run is doing the work."""

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
    dedupe_key: str | None = None
    created_at: datetime
    updated_at: datetime
    execution_name: str | None = None
    results_prefix: str
    summary: RunSummary | None = None
    artifacts: list[ArtifactRef] = []
    error: str | None = None


# --- Per-agent input schemas ---


class BugFixInputs(BaseModel):
    bug_id: int
    # When following up on an existing Phabricator revision (e.g. triggered by a
    # webhook), the revision to update and the comment that mentioned Hackbot.
    # Both are omitted for a plain "fix this bug" run and for Bugzilla needinfo.
    revision_id: int | None = None
    comment: str | None = None
    # Set only by a Bugzilla flag.needinfo webhook. Its presence selects the
    # follow-up mode and lets the API clear that exact flag after the response.
    bugzilla_needinfo_flag_id: Annotated[int | None, Field(gt=0)] = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    @model_validator(mode="after")
    def _validate_mode(self) -> "BugFixInputs":
        """Require exactly one coherent normal, Phabricator, or Bugzilla mode."""
        if self.bugzilla_needinfo_flag_id is not None:
            if self.revision_id is not None:
                raise ValueError(
                    "bugzilla_needinfo_flag_id cannot be combined with revision_id"
                )
            if not self.comment:
                raise ValueError(
                    "comment is required when bugzilla_needinfo_flag_id is set"
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


class AutowebcompatDiagnosisInputs(BaseModel):
    bug_data: str | None = None
    bug_id: int | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

    @model_validator(mode="after")
    def _require_subject(self) -> "AutowebcompatDiagnosisInputs":
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


class GitUpliftSource(BaseModel):
    """A patch to uplift, identified by a commit already in the Firefox repo."""

    # Discriminator tag selecting this variant in the `UpliftSource` union.
    kind: Literal["git"] = "git"

    # Full git commit SHA the agent fetches and cherry-picks onto the branch.
    commit: str = Field(description="Full git commit SHA to cherry-pick.")


class PhabricatorUpliftSource(BaseModel):
    """A patch to uplift, identified by a Phabricator revision.

    The agent fetches the diff through its broker; inputs reach the job as
    environment variables, which a raw diff would not fit.
    """

    # Discriminator tag selecting this variant in the `UpliftSource` union.
    kind: Literal["phabricator"] = "phabricator"

    # Phabricator revision to uplift, used for context and commit text.
    revision_id: int = Field(description="Phabricator revision id (the D-number).")

    # Pin the exact diff. Without one, a revision updated since the request
    # resolves to different code.
    diff_id: int | None = Field(
        default=None,
        description="Diff to uplift; defaults to the revision's latest.",
    )


# `kind` discriminates the two, so one run can mix both.
UpliftSource = Annotated[
    Union[GitUpliftSource, PhabricatorUpliftSource], Field(discriminator="kind")
]


class UpliftInputs(BaseModel):
    """Inputs for the uplift conflict-resolution agent."""

    # The stable branch ref to uplift onto, e.g. `release`, `beta`, `esr128`.
    target_branch: str

    # The exact commit to uplift onto. Branch names move, so a caller
    # reproducing a specific uplift should pin it; otherwise the tip is used.
    target_commit: str | None = None

    # Ordered patches to apply onto the branch; applied in this sequence.
    sources: list[UpliftSource]

    # Originating Bugzilla bug, supplied as extra context for the agent.
    bug_id: int | None = None

    # Override the agent's default Claude model id.
    model: str | None = None

    # Cap on agent turns; `None` leaves the agent's own default in place.
    max_turns: int | None = None

    # Override the agent's default reasoning effort (e.g. `high`).
    effort: str | None = None

    @model_validator(mode="after")
    def require_sources(self) -> "UpliftInputs":
        if not self.sources:
            raise ValueError("provide at least one source to uplift")
        return self
