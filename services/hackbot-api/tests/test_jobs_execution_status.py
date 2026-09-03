"""Tests for mapping a Cloud Run execution to an ExecutionStatus."""

from unittest.mock import MagicMock

from app import jobs
from app.jobs import ExecutionStatus
from google.api_core.exceptions import NotFound


def test_deleted_execution_maps_to_gone(monkeypatch):
    """A 404 is permanent, not transient.

    Retrying it forever is what caused the poison loop; callers treat `gone`
    as terminal and recover the run's outcome from summary.json instead.
    """
    client = MagicMock()
    client.get_execution.side_effect = NotFound("execution does not exist")
    monkeypatch.setattr(jobs, "_executions_client", lambda: client)

    status = jobs._execution_status_sync("projects/p/../executions/e")

    assert status is ExecutionStatus.gone


def test_completed_execution_still_maps_normally(monkeypatch):
    execution = MagicMock(
        completion_time="2026-08-31T00:00:00Z", succeeded_count=1, failed_count=0
    )
    client = MagicMock()
    client.get_execution.return_value = execution
    monkeypatch.setattr(jobs, "_executions_client", lambda: client)

    assert jobs._execution_status_sync("e") is ExecutionStatus.succeeded
