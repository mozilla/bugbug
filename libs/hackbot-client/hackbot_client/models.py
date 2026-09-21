"""Typed models for the public Hackbot API contract."""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel

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
