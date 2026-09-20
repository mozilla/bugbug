"""Typed models for the public Hackbot API contract."""

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, RootModel

# Duplicated in services/hackbot-api/app/schemas.py; keep these models in sync.


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    timed_out = "timed_out"


class RunRef(BaseModel):
    run_id: UUID
    agent: str
    status: RunStatus


class TriggeredRun(RunRef):
    """The run a trigger resolved to, and whether that request is what started it.

    `is_new` is a property of the request rather than of the run, and says
    nothing about the run's `status`: the same run is `is_new` to the request
    that created it and not to every later request carrying its `dedupe_key`.
    That is why it is not on the API's own `RunRef`. Subclasses `RunRef` so a
    caller that only wants the run keeps reading `run_id` off it unchanged.
    """

    is_new: bool


class RunAction(BaseModel):
    """One recorded action of a run, and whether it has landed.

    Mirrors the API's `RunActionDoc`. `status` is `"applied"` or `"failed"`:
    the apply endpoint answers `200` for a pass that ran, not for a pass whose
    every action succeeded, so a caller that needs the actions to have *landed*
    has to read this rather than the status code.
    """

    idx: int
    type: str
    status: str
    ref: str | None = None
    error: str | None = None
    applied_at: datetime | None = None

    @property
    def is_applied(self) -> bool:
        return self.status == "applied"

    def __str__(self) -> str:
        return self.type if self.is_applied else f"{self.type} ({self.error})"


class ApplyActionsResponse(RootModel[list[RunAction]]):
    """A run's actions and their apply state, as the API returns them."""

    def __iter__(self):
        return iter(self.root)

    def __len__(self) -> int:
        return len(self.root)

    @property
    def unapplied(self) -> list[RunAction]:
        return [action for action in self.root if not action.is_applied]

    @property
    def all_applied(self) -> bool:
        return not self.unapplied

    @property
    def failure_summary(self) -> str:
        """The actions that did not land, with the reason each gave."""
        return "; ".join(str(action) for action in self.unapplied)
