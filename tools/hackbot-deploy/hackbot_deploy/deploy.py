"""Ship agent images onto their infrastructure.

Terraform owns an agent's infrastructure; this owns which image runs on it.
Deploying, promoting and rolling back are all the same operation: set the
images an agent runs in one environment, then record that in the
environment's registry bucket.

The gate. Every image carries the ``config_hash`` of the shape it was built
for (the ``hackbot.config_hash`` label), and every environment's Terraform
publishes the shape it has applied (``applied.json``). A forward deploy needs
the two to match; otherwise the image would land on infrastructure it wasn't
built for, so the deploy waits for the infra PR to be applied. A rollback
needs the image's hash to be in ``compatible``, the shapes the current
infrastructure can still run.

Images are always pinned by digest. A tag can be moved by anyone who can push;
a digest can't.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

REGION = "us-west1"
LABEL_CONFIG_HASH = "hackbot.config_hash"
LABEL_REVISION = "org.opencontainers.image.revision"
LABEL_CREATED = "org.opencontainers.image.created"
JOB_LABEL_CONFIG_HASH = "hackbot-config-hash"
HISTORY_LIMIT = 10


@dataclass(frozen=True)
class Environment:
    """One hackbot environment.

    These names mirror the Terraform in webservices-infra (hackbot/tf): dev
    and stage share the nonprod project and carry the environment as a name
    suffix, prod has its project to itself. Change one, change both.
    """

    name: str
    project_id: str
    name_suffix: str

    @property
    def registry_bucket(self) -> str:
        return f"{self.project_id}-registry{self.name_suffix}"

    def job_name(self, agent: str) -> str:
        return f"hackbot-agent-{agent}{self.name_suffix}"


ENVIRONMENTS = {
    "dev": Environment("dev", "moz-fx-hackbot-nonprod", "-dev"),
    "stage": Environment("stage", "moz-fx-hackbot-nonprod", "-stage"),
    "prod": Environment("prod", "moz-fx-hackbot-prod", ""),
}


class DeployError(Exception):
    """The deploy can't go ahead, and won't until something is fixed."""


class WaitingForInfra(DeployError):
    """The image's shape isn't applied in this environment yet.

    Not a failure: the deploy can be rerun once the infra PR is applied.
    """


@dataclass
class StoredObject:
    data: bytes
    generation: int


class Cloud(Protocol):
    """What the deploy needs from GCP. The real one is :class:`GoogleCloud`."""

    def read_object(self, bucket: str, name: str) -> StoredObject | None: ...

    def write_object(
        self, bucket: str, name: str, data: bytes, if_generation_match: int
    ) -> None:
        """Write, but only if the object is still at that generation (0: absent)."""

    def image_labels(self, image: str) -> dict[str, str]: ...

    def get_job(self, project: str, region: str, name: str) -> dict[str, Any]: ...

    def set_job_images(
        self, project: str, region: str, name: str, images: dict[str, str]
    ) -> None:
        """Set container images by container name, and wait until done."""


@dataclass
class Result:
    agent: str
    environment: str
    action: str  # "deployed", "rolled back", "unchanged"
    images: dict[str, str]
    config_hash: str
    record: dict[str, Any] = field(default_factory=dict)


def require_digest(image: str) -> str:
    """Refuse anything not pinned by digest."""
    if "@sha256:" not in image:
        raise DeployError(
            f"{image}: images must be pinned by digest (…@sha256:…), not by tag"
        )
    return image


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _applied(cloud: Cloud, env: Environment, agent: str) -> dict[str, Any]:
    stored = cloud.read_object(env.registry_bucket, "applied.json")
    if stored is None:
        raise DeployError(
            f"{env.name}: no applied.json in gs://{env.registry_bucket}; has Terraform been applied?"
        )
    agents = json.loads(stored.data)["agents"]
    if agent not in agents:
        raise WaitingForInfra(f"{agent} isn't applied in {env.name} yet")
    return agents[agent]


def _image_hash(cloud: Cloud, images: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Return the config hash every image agrees on, and the agent image's labels."""
    labels = {container: cloud.image_labels(ref) for container, ref in images.items()}
    hashes = {
        container: lbl.get(LABEL_CONFIG_HASH) for container, lbl in labels.items()
    }
    missing = [container for container, h in hashes.items() if not h]
    if missing:
        raise DeployError(
            f"no {LABEL_CONFIG_HASH} label on the {', '.join(missing)} image; was it built by the agent pipeline?"
        )
    if len(set(hashes.values())) != 1:
        raise DeployError(f"the images were built for different shapes: {hashes}")
    return next(iter(hashes.values())), labels["agent"]  # type: ignore[return-value]


def _read_record(
    cloud: Cloud, env: Environment, agent: str
) -> tuple[dict[str, Any] | None, int]:
    stored = cloud.read_object(env.registry_bucket, f"deployments/{agent}.json")
    if stored is None:
        return None, 0
    return json.loads(stored.data), stored.generation


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        k: record[k]
        for k in ("images", "config_hash", "revision", "created", "deployed_at")
        if k in record
    }


def deploy_agent(
    cloud: Cloud,
    agent: str,
    env: Environment,
    images: dict[str, str] | None = None,
    *,
    rollback: bool = False,
    force: bool = False,
    dry_run: bool = False,
    actor: str = "unknown",
) -> Result:
    """Put ``images`` on ``agent`` in ``env``, or roll back to the last deployment.

    ``images`` maps container name to image digest: ``agent``, plus ``broker``
    for agents that have one. With ``rollback``, leave ``images`` out to go
    back to the deployment before the current one.
    """
    current, generation = _read_record(cloud, env, agent)

    if rollback and images is None:
        if not current or not current.get("history"):
            raise DeployError(
                f"{agent} in {env.name} has no earlier deployment to roll back to"
            )
        images = dict(current["history"][0]["images"])
    if not images or "agent" not in images:
        raise DeployError("an agent image is required")
    images = {container: require_digest(ref) for container, ref in images.items()}

    applied = _applied(cloud, env, agent)
    image_hash, labels = _image_hash(cloud, images)

    if rollback:
        if image_hash not in applied["compatible"]:
            raise DeployError(
                f"can't roll {agent} back in {env.name}: the image was built for {image_hash}, "
                f"and the applied infrastructure only runs {applied['compatible']} (a contract cut it off)"
            )
    elif image_hash != applied["applied"]:
        raise WaitingForInfra(
            f"{agent} in {env.name}: the image was built for shape {image_hash}, but {applied['applied']} is applied. "
            "Deploy again once the infra PR for this change is applied."
        )

    if current and current.get("images") == images:
        return Result(agent, env.name, "unchanged", images, image_hash, current)

    # Builds aren't serialised, so an older build can finish last. Don't let
    # it replace a newer one, unless that's the point (a rollback, or --force).
    created = labels.get(LABEL_CREATED)
    if (
        not rollback
        and not force
        and current
        and created
        and current.get("created")
        and created < current["created"]
    ):
        raise DeployError(
            f"{agent} in {env.name} already runs an image built at {current['created']}; "
            f"this one was built at {created}. Use --force to deploy it anyway."
        )

    job_name = env.job_name(agent)
    job = cloud.get_job(env.project_id, REGION, job_name)
    job_hash = job.get("labels", {}).get(JOB_LABEL_CONFIG_HASH)
    if job_hash and job_hash != applied["applied"]:
        raise DeployError(
            f"{job_name} is labelled {job_hash} but applied.json says {applied['applied']}; "
            "a Terraform apply looks incomplete"
        )
    containers = {c["name"] for c in job["template"]["template"]["containers"]}
    if containers != set(images):
        raise DeployError(
            f"{job_name} runs containers {sorted(containers)}, but images were given for {sorted(images)}"
        )

    if rollback:
        # The target leaves the history; the deployment being rolled back
        # doesn't join it, so rolling back twice goes back two deployments.
        history = [
            h for h in (current or {}).get("history", []) if h.get("images") != images
        ]
    else:
        history = ([_summary(current)] + current.get("history", [])) if current else []
    record = {
        "agent": agent,
        "environment": env.name,
        "runtime": applied["runtime"],
        "images": images,
        "config_hash": image_hash,
        "revision": labels.get(LABEL_REVISION),
        "created": created,
        "deployed_at": _now(),
        "deployed_by": actor,
        "history": history[:HISTORY_LIMIT],
    }
    action = "rolled back" if rollback else "deployed"
    if dry_run:
        return Result(agent, env.name, f"would be {action}", images, image_hash, record)

    cloud.set_job_images(env.project_id, REGION, job_name, images)
    after = cloud.get_job(env.project_id, REGION, job_name)
    running = {
        c["name"]: c["image"] for c in after["template"]["template"]["containers"]
    }
    if running != images:
        raise DeployError(
            f"{job_name} reports {running} after the update, not {images}"
        )

    cloud.write_object(
        env.registry_bucket,
        f"deployments/{agent}.json",
        (json.dumps(record, indent=2) + "\n").encode(),
        if_generation_match=generation,
    )
    return Result(agent, env.name, action, images, image_hash, record)


def promote_agent(
    cloud: Cloud, agent: str, source: Environment, target: Environment, **kwargs: Any
) -> Result:
    """Deploy to ``target`` exactly the images ``agent`` runs in ``source``."""
    record, _ = _read_record(cloud, source, agent)
    if record is None:
        raise DeployError(f"{agent} has never been deployed to {source.name}")
    return deploy_agent(cloud, agent, target, dict(record["images"]), **kwargs)


class GoogleCloud:
    """:class:`Cloud` over the GCP REST APIs, with Application Default Credentials.

    No gcloud: ADC works the same from Cloud Build (the build's service
    account), GitHub Actions (Workload Identity Federation) and a laptop.
    """

    _MANIFEST_TYPES = ", ".join(
        [
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ]
    )

    def __init__(
        self,
        session: Any = None,
        poll_seconds: float = 3.0,
        timeout_seconds: float = 600.0,
    ) -> None:
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            session = AuthorizedSession(credentials)
        self.session = session
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds

    def _check(self, response: Any, what: str) -> Any:
        if response.status_code >= 400:
            raise DeployError(
                f"{what}: HTTP {response.status_code}: {response.text[:500]}"
            )
        return response

    def read_object(self, bucket: str, name: str) -> StoredObject | None:
        url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(name, safe='')}"
        meta = self.session.get(url)
        if meta.status_code == 404:
            return None
        generation = int(
            self._check(meta, f"gs://{bucket}/{name}").json()["generation"]
        )
        # Read that exact generation, so a write conditioned on it can't be
        # based on content newer than the generation it names.
        media = self.session.get(url, params={"alt": "media", "generation": generation})
        return StoredObject(
            self._check(media, f"gs://{bucket}/{name}").content, generation
        )

    def write_object(
        self, bucket: str, name: str, data: bytes, if_generation_match: int
    ) -> None:
        response = self.session.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
            params={
                "uploadType": "media",
                "name": name,
                "ifGenerationMatch": if_generation_match,
            },
            data=data,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code == 412:
            raise DeployError(
                f"gs://{bucket}/{name} changed during the deploy; another deploy ran concurrently. Retry."
            )
        self._check(response, f"writing gs://{bucket}/{name}")

    def image_labels(self, image: str) -> dict[str, str]:
        reference, digest = image.split("@", 1)
        host, path = reference.split("/", 1)
        base = f"https://{host}/v2/{path}"
        manifest = self._check(
            self.session.get(
                f"{base}/manifests/{digest}", headers={"Accept": self._MANIFEST_TYPES}
            ),
            f"manifest of {image}",
        ).json()
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
            manifest = self._check(
                self.session.get(
                    f"{base}/manifests/{platforms[0]['digest']}",
                    headers={"Accept": self._MANIFEST_TYPES},
                ),
                f"amd64 manifest of {image}",
            ).json()
        config = self._check(
            self.session.get(f"{base}/blobs/{manifest['config']['digest']}"),
            f"config of {image}",
        ).json()
        return config.get("config", {}).get("Labels") or {}

    def get_job(self, project: str, region: str, name: str) -> dict[str, Any]:
        url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{name}"
        return self._check(self.session.get(url), f"Cloud Run Job {name}").json()

    def set_job_images(
        self, project: str, region: str, name: str, images: dict[str, str]
    ) -> None:
        job = self.get_job(project, region, name)
        for container in job["template"]["template"]["containers"]:
            container["image"] = images[container["name"]]
        url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{name}"
        operation = self._check(
            self.session.patch(url, json=job), f"updating {name}"
        ).json()
        deadline = time.monotonic() + self.timeout_seconds
        while not operation.get("done"):
            if time.monotonic() > deadline:
                raise DeployError(
                    f"updating {name} didn't finish in {self.timeout_seconds:.0f}s ({operation['name']})"
                )
            time.sleep(self.poll_seconds)
            operation = self._check(
                self.session.get(f"https://run.googleapis.com/v2/{operation['name']}"),
                f"waiting on {name}",
            ).json()
        if "error" in operation:
            raise DeployError(f"updating {name} failed: {operation['error']}")
