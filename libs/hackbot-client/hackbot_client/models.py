"""Typed models for the public Hackbot API contract."""

from enum import Enum
from uuid import UUID

from pydantic import BaseModel


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
