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
