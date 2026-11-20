"""Tests for mapping a Cloud Run execution to an ExecutionStatus."""

from unittest.mock import MagicMock

from app import jobs
from app.jobs import ExecutionStatus
from google.api_core.exceptions import NotFound, ServiceUnavailable
from google.cloud import run_v2


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


def test_job_names_carry_the_environment_suffix(monkeypatch):
    monkeypatch.setattr(jobs.settings, "gcp_project", "p")
    monkeypatch.setattr(jobs.settings, "gcp_region", "us-west1")
    monkeypatch.setattr(jobs.settings, "agent_job_suffix", "-dev")

    assert (
        jobs._job_resource_name("hackbot-agent-bug-fix")
        == "projects/p/locations/us-west1/jobs/hackbot-agent-bug-fix-dev"
    )


def test_unavailable_run_job_reuses_execution_that_already_started(monkeypatch):
    monkeypatch.setattr(jobs.settings, "gcp_project", "p")
    monkeypatch.setattr(jobs.settings, "gcp_region", "r")
    jobs_client = MagicMock()
    jobs_client.run_job.side_effect = ServiceUnavailable("Connection reset by peer")
    started = run_v2.Execution(
        name="executions/started",
        template=run_v2.TaskTemplate(
            containers=[
                run_v2.Container(
                    name="agent", env=[run_v2.EnvVar(name="RUN_ID", value="run-1")]
                )
            ]
        ),
    )
    executions_client = MagicMock()
    executions_client.list_executions.return_value.executions = [started]
    monkeypatch.setattr(jobs, "_jobs_client", lambda: jobs_client)
    monkeypatch.setattr(jobs, "_executions_client", lambda: executions_client)

    name = jobs._trigger_sync("job", {"RUN_ID": "run-1"}, 60)

    assert name == "executions/started"
    assert jobs_client.run_job.call_count == 1
