"""The real :class:`~hackbot_deploy.deploy.Cloud`, over Google's client libraries.

No gcloud: Application Default Credentials work the same from Cloud Build (the
build's service account), GitHub Actions (Workload Identity Federation) and a
laptop. The clients return protobuf messages; they are handed to the deploy
logic as the dicts the REST API would return, so it and its test fake don't
depend on the client types.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import google.auth
from google.api_core.exceptions import GoogleAPICallError, PreconditionFailed
from google.auth.transport.requests import AuthorizedSession
from google.cloud import run_v2, storage
from google.cloud.devtools import cloudbuild_v1

from hackbot_deploy.deploy import DeployError, StoredObject, split_image

# Artifact Registry speaks the OCI distribution API, which has no client
# library; image labels are read from it directly, with the deploy's
# credentials. So they only go to Artifact Registry hosts, in any region.
_REGISTRY_HOST_SUFFIX = "-docker.pkg.dev"
_MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


@contextmanager
def _calling(what: str) -> Iterator[None]:
    """Turn a failed API call into a DeployError, which the CLI reports."""
    try:
        yield
    except GoogleAPICallError as exc:
        raise DeployError(f"{what}: {exc}") from exc
    except TimeoutError as exc:
        raise DeployError(f"{what}: timed out") from exc


def _as_dict(message: Any) -> dict[str, Any]:
    return type(message).to_dict(
        message, preserving_proto_field_name=False, use_integers_for_enums=False
    )


class GoogleCloud:
    def __init__(self, timeout_seconds: float = 600.0) -> None:
        credentials, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        # Buckets are addressed by name, so no project is needed; an explicit
        # None stops the client failing when the credentials don't carry one.
        self.storage = storage.Client(project=project, credentials=credentials)
        self.jobs = run_v2.JobsClient(credentials=credentials)
        self.services = run_v2.ServicesClient(credentials=credentials)
        self.builds = cloudbuild_v1.CloudBuildClient(credentials=credentials)
        self.registry = AuthorizedSession(credentials)
        self.timeout_seconds = timeout_seconds

    # --- Storage ---

    def read_object(self, bucket: str, name: str) -> StoredObject | None:
        with _calling(f"reading gs://{bucket}/{name}"):
            blob = self.storage.bucket(bucket).get_blob(name)
            if blob is None:
                return None
            # Read the generation the metadata named, so a write conditioned
            # on it can't be based on newer content.
            data = blob.download_as_bytes(if_generation_match=blob.generation)
        return StoredObject(data, blob.generation)

    def write_object(
        self, bucket: str, name: str, data: bytes, if_generation_match: int
    ) -> None:
        with _calling(f"writing gs://{bucket}/{name}"):
            try:
                self.storage.bucket(bucket).blob(name).upload_from_string(
                    data,
                    content_type="application/json",
                    if_generation_match=if_generation_match,
                )
            except PreconditionFailed:
                raise DeployError(
                    f"gs://{bucket}/{name} changed during the deploy; another deploy ran concurrently. Retry."
                ) from None

    # --- Artifact Registry ---

    def _registry_get(self, url: str, what: str, **kwargs: Any) -> dict[str, Any]:
        response = self.registry.get(url, **kwargs)
        if response.status_code >= 400:
            raise DeployError(
                f"{what}: HTTP {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    def image_labels(self, image: str) -> dict[str, str]:
        # split_image allows only DNS-label characters in the host, so a host
        # with this suffix can only be under pkg.dev.
        host, path, digest = split_image(image)
        if not host.endswith(_REGISTRY_HOST_SUFFIX):
            raise DeployError(
                f"{image}: images must be in Artifact Registry (<region>{_REGISTRY_HOST_SUFFIX})"
            )
        base = f"https://{host}/v2/{path}"
        accept = {"Accept": _MANIFEST_TYPES}
        manifest = self._registry_get(
            f"{base}/manifests/{digest}", f"manifest of {image}", headers=accept
        )
        if "manifests" in manifest:  # a multi-platform index
            platforms = [
                m
                for m in manifest["manifests"]
                if m.get("platform", {}) == {"architecture": "amd64", "os": "linux"}
            ] or [
                m
                for m in manifest["manifests"]
                if m.get("platform", {}).get("architecture") == "amd64"
            ]
            if not platforms:
                raise DeployError(f"{image} has no linux/amd64 image")
            manifest = self._registry_get(
                f"{base}/manifests/{platforms[0]['digest']}",
                f"amd64 manifest of {image}",
                headers=accept,
            )
        config = self._registry_get(
            f"{base}/blobs/{manifest['config']['digest']}", f"config of {image}"
        )
        return config.get("config", {}).get("Labels") or {}

    # --- Cloud Run ---

    def get_job(self, project: str, region: str, name: str) -> dict[str, Any]:
        with _calling(f"reading Cloud Run Job {name}"):
            return _as_dict(
                self.jobs.get_job(name=self.jobs.job_path(project, region, name))
            )

    def set_job_images(
        self, project: str, region: str, name: str, images: dict[str, str]
    ) -> None:
        with _calling(f"updating Cloud Run Job {name}"):
            job = self.jobs.get_job(name=self.jobs.job_path(project, region, name))
            for container in job.template.template.containers:
                container.image = images[container.name]
            self.jobs.update_job(job=job).result(timeout=self.timeout_seconds)

    def get_service(self, project: str, region: str, name: str) -> dict[str, Any]:
        with _calling(f"reading Cloud Run service {name}"):
            return _as_dict(
                self.services.get_service(
                    name=self.services.service_path(project, region, name)
                )
            )

    def set_service_images(
        self, project: str, region: str, name: str, images: dict[str, str]
    ) -> None:
        with _calling(f"updating Cloud Run service {name}"):
            service = self.services.get_service(
                name=self.services.service_path(project, region, name)
            )
            for container in service.template.containers:
                container.image = images[container.name]
            self.services.update_service(service=service).result(
                timeout=self.timeout_seconds
            )

    # --- Cloud Build ---

    def get_build(self, project: str, location: str, build_id: str) -> dict[str, Any]:
        with _calling(f"reading build {build_id}"):
            build = self.builds.get_build(
                request={
                    "name": f"projects/{project}/locations/{location}/builds/{build_id}",
                    "project_id": project,
                    "id": build_id,
                }
            )
        return _as_dict(build)

    def ongoing_builds(
        self, project: str, location: str, trigger_id: str
    ) -> list[dict[str, Any]]:
        with _calling(f"listing builds of trigger {trigger_id}"):
            return [
                _as_dict(build)
                for build in self.builds.list_builds(
                    request={
                        "parent": f"projects/{project}/locations/{location}",
                        "project_id": project,
                        "filter": f'trigger_id="{trigger_id}" AND (status="QUEUED" OR status="WORKING")',
                    }
                )
            ]
