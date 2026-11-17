from typing import Any

import pytest
from hackbot_deploy.deploy import DeployError
from hackbot_deploy.gcp import GoogleCloud

D = "sha256:" + "0" * 64


class RecordingSession:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> Any:
        self.urls.append(url)
        raise AssertionError("no request expected")


def cloud_with(session: RecordingSession) -> GoogleCloud:
    # Only the registry session is needed; skip the real clients and ADC.
    cloud = GoogleCloud.__new__(GoogleCloud)
    cloud.registry = session
    return cloud


@pytest.mark.parametrize(
    "ref",
    [
        f"evil.example/bug-fix-agent@{D}",
        f"us-docker.pkg.dev.evil.example/x@{D}",
        f"ghcr.io/mozilla/bugbug@{D}",
    ],
)
def test_labels_are_only_read_from_artifact_registry(ref: str) -> None:
    session = RecordingSession()
    with pytest.raises(DeployError, match="Artifact Registry"):
        cloud_with(session).image_labels(ref)
    assert session.urls == []


@pytest.mark.parametrize(
    "host", ["us-docker.pkg.dev", "us-west1-docker.pkg.dev", "europe-docker.pkg.dev"]
)
def test_any_artifact_registry_region_is_read(host: str) -> None:
    session = RecordingSession()
    with pytest.raises(AssertionError):
        cloud_with(session).image_labels(f"{host}/p/r/agent@{D}")
    assert session.urls == [f"https://{host}/v2/p/r/agent/manifests/{D}"]


class FakeJobs:
    """Stands in for run_v2.JobsClient: run_job returns an operation whose result is the execution."""

    def __init__(self, succeeded: int, failed: int) -> None:
        self.execution = type(
            "Execution",
            (),
            {
                "name": "projects/p/locations/r/jobs/j/executions/j-x7k2p",
                "succeeded_count": succeeded,
                "failed_count": failed,
            },
        )()
        self.ran: list[str] = []

    def job_path(self, project: str, region: str, name: str) -> str:
        return f"projects/{project}/locations/{region}/jobs/{name}"

    def run_job(self, name: str) -> Any:
        self.ran.append(name)
        execution = self.execution
        return type("Operation", (), {"result": lambda self, timeout: execution})()


def cloud_with_jobs(jobs: FakeJobs) -> GoogleCloud:
    cloud = GoogleCloud.__new__(GoogleCloud)
    cloud.jobs = jobs
    cloud.timeout_seconds = 600
    return cloud


def test_a_successful_execution_returns() -> None:
    jobs = FakeJobs(succeeded=1, failed=0)
    cloud_with_jobs(jobs).run_job("p", "r", "hackbot-api-migrate-dev")
    assert jobs.ran == ["projects/p/locations/r/jobs/hackbot-api-migrate-dev"]


@pytest.mark.parametrize(("succeeded", "failed"), [(0, 1), (0, 0)])
def test_an_unsuccessful_execution_fails_naming_it(succeeded: int, failed: int) -> None:
    jobs = FakeJobs(succeeded=succeeded, failed=failed)
    with pytest.raises(
        DeployError, match="hackbot-api-migrate-dev: execution j-x7k2p failed"
    ):
        cloud_with_jobs(jobs).run_job("p", "r", "hackbot-api-migrate-dev")
