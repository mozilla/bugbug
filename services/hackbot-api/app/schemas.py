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


class Confidence(str, Enum):
    """How sure an agent is of its own findings.

    Defined once here because two modules act on it: the applier decides whether to
    write to Bugzilla unattended, and the notifier repeats it to the channel. Reported
    by the agent inside a free-form JSON block, so it arrives as untrusted text — use
    `parse_confidence` rather than the constructor.
    """

    high = "high"
    medium = "medium"
    low = "low"


def parse_confidence(value: object) -> Confidence | None:
    """`value` as a `Confidence`, or None if it isn't one.

    Tolerates casing and stray whitespace, because the value is parsed out of
    model output and "High" must not silently read as "no confidence". Anything
    else is None: callers fail closed rather than inventing a level.
    """
    if not isinstance(value, str):
        return None
    try:
        return Confidence(value.strip().lower())
    except ValueError:
        return None


class RunActionOutcome(str, Enum):
    """What became of a completed run's recorded actions.

    "We decided to apply" and "Bugzilla accepted it" are deliberately distinct: a
    reader being told about a run needs to know which of the two happened.
    """

    posted = "posted"  # every action landed on Bugzilla
    held = "held"  # not applied; awaiting human review
    failed = "failed"  # apply ran but at least one action did not land
    no_actions = "no_actions"  # the run recorded nothing to apply


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
