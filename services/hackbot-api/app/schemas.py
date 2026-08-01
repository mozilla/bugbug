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


class FeedbackRating(str, Enum):
    up = "up"
    down = "down"


class RaterKind(str, Enum):
    anonymous = "anonymous"
    bugzilla = "bugzilla"


class FeedbackDimension(str, Enum):
    """What a rater says went wrong, keyed to the agent's own output fields.

    Members map 1:1 onto the structured plan the agent emits (root_cause,
    proposed_fix, target_files, confidence, actionable), so a thumbs-down can be
    aggregated per-field instead of read as prose.
    """

    root_cause_wrong = "root_cause_wrong"
    fix_wont_work = "fix_wont_work"
    wrong_files = "wrong_files"
    overconfident = "overconfident"
    should_not_have_commented = "should_not_have_commented"
    too_verbose = "too_verbose"


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


# --- Public feedback on a posted Bugzilla comment ---


class FeedbackTargetDoc(BaseModel):
    """What the public feedback page needs to render, and nothing else.

    Deliberately narrow: the page is reachable without authentication, so it
    gets the comment that was actually posted and the bug it landed on — never
    the run's inputs, findings, artifacts or identity.
    """

    bug_id: int
    comment: str
    # Gates the write (see app/feedback_links.py). Minted per page render so a
    # crawler that only ever GETs the link cannot cast a vote.
    nonce: str


class FeedbackCreate(BaseModel):
    rating: FeedbackRating
    nonce: str
    dimensions: list[FeedbackDimension] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=5000)


class FeedbackResponse(BaseModel):
    message: str


class FeedbackDoc(BaseModel):
    """One recorded rating, as shown on the internal (SSO-gated) review page."""

    run_id: UUID
    agent: str
    bug_id: int | None = None
    rating: FeedbackRating
    dimensions: list[str] = Field(default_factory=list)
    comment: str | None = None
    created_at: datetime


# --- Per-agent input schemas ---


class BugFixInputs(BaseModel):
    bug_id: int
    # When following up on an existing Phabricator revision (e.g. triggered by a
    # webhook), the revision to update and the comment that mentioned Hackbot, to
    # act on. Both optional: omitted for a plain "fix this bug" run.
    revision_id: int | None = None
    comment: str | None = None
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None


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
