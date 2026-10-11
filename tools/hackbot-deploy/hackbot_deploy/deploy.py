"""Ship images onto hackbot's infrastructure: agents and the API and UI.

Terraform owns the infrastructure; this owns which image runs on it.
Deploying, promoting and rolling back are all the same operation: set the
images something runs in one environment, then record that in the
environment's registry bucket (``deployments/<name>.json``).

The gate, for agents. Every agent image carries the ``config_hash`` of the
shape it was built for (the ``hackbot.config_hash`` label), and every
environment's Terraform publishes the shape it has applied (``applied.json``).
A forward deploy needs the two to match; otherwise the image would land on
infrastructure it wasn't built for, so the deploy waits for the infra PR to be
applied. A rollback needs the image's hash to be in ``compatible``, the shapes
the current infrastructure can still run.

The API and UI have no gate. Their infrastructure is written by hand in
webservices-infra and changes there first, so an infra change that new code
depends on is simply applied before the code ships.

Images are always pinned by digest. A tag can be moved by anyone who can push;
a digest can't.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
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

# Deployed as Cloud Run services; everything else is an agent.
SERVICES = ("hackbot-api", "hackbot-ui")


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

    def service_name(self, service: str) -> str:
        return f"{service}{self.name_suffix}"


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

    def get_service(self, project: str, region: str, name: str) -> dict[str, Any]: ...

    def set_service_images(
        self, project: str, region: str, name: str, images: dict[str, str]
    ) -> None:
        """Set container images by container name, and wait for the new revision."""

    def get_build(
        self, project: str, location: str, build_id: str
    ) -> dict[str, Any]: ...

    def ongoing_builds(
        self, project: str, location: str, trigger_id: str
    ) -> list[dict[str, Any]]:
        """The trigger's builds that are queued or running."""


@dataclass
class Result:
    name: str
    environment: str
    action: str  # "deployed", "rolled back", "unchanged", "would be …"
    images: dict[str, str]
    config_hash: str | None
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


def _read_record(
    cloud: Cloud, env: Environment, name: str
) -> tuple[dict[str, Any] | None, int]:
    stored = cloud.read_object(env.registry_bucket, f"deployments/{name}.json")
    if stored is None:
        return None, 0
    return json.loads(stored.data), stored.generation


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        k: record[k]
        for k in ("images", "config_hash", "revision", "created", "deployed_at")
        if k in record
    }


# A rollback that took images would skip the stale-build check and put any
# compatible image on an environment without going through a deploy.
_ROLLBACK_WITH_IMAGES = (
    "a rollback only goes back through the recorded deployments and takes no "
    "images; deploy a specific image with deploy"
)


def _rollback_target(
    current: dict[str, Any] | None, name: str, env: Environment
) -> dict[str, str]:
    if not current or not current.get("history"):
        raise DeployError(
            f"{name} in {env.name} has no earlier deployment to roll back to"
        )
    return dict(current["history"][0]["images"])


def _refuse_stale(
    current: dict[str, Any] | None,
    created: str | None,
    name: str,
    env: Environment,
    *,
    rollback: bool,
    force: bool,
) -> None:
    # Builds aren't serialised, so an older build can finish last. Don't let
    # it replace a newer one, unless that's the point (a rollback, or --force).
    if rollback or force or not current or not created or not current.get("created"):
        return
    if created < current["created"]:
        raise DeployError(
            f"{name} in {env.name} already runs an image built at {current['created']}; "
            f"this one was built at {created}. Use --force to deploy it anyway."
        )


def _history(
    current: dict[str, Any] | None, images: dict[str, str], *, rollback: bool
) -> list[dict[str, Any]]:
    if rollback:
        # The target leaves the history; the deployment being rolled back
        # doesn't join it, so rolling back twice goes back two deployments.
        return [
            h for h in (current or {}).get("history", []) if h.get("images") != images
        ]
    return ([_summary(current)] + current.get("history", [])) if current else []


def _ship(
    cloud: Cloud,
    env: Environment,
    name: str,
    *,
    images: dict[str, str],
    config_hash: str | None,
    runtime: str,
    labels: dict[str, str],
    current: dict[str, Any] | None,
    generation: int,
    rollback: bool,
    dry_run: bool,
    actor: str,
    update: Callable[[], None],
) -> Result:
    """Build the deployment record, apply the update, and write the record."""
    record = {
        "name": name,
        "environment": env.name,
        "runtime": runtime,
        "images": images,
        "config_hash": config_hash,
        "revision": labels.get(LABEL_REVISION),
        "created": labels.get(LABEL_CREATED),
        "deployed_at": _now(),
        "deployed_by": actor,
        "history": _history(current, images, rollback=rollback)[:HISTORY_LIMIT],
    }
    action = "rolled back" if rollback else "deployed"
    if dry_run:
        return Result(name, env.name, f"would be {action}", images, config_hash, record)

    update()
    cloud.write_object(
        env.registry_bucket,
        f"deployments/{name}.json",
        (json.dumps(record, indent=2) + "\n").encode(),
        if_generation_match=generation,
    )
    return Result(name, env.name, action, images, config_hash, record)


# --- Agents ------------------------------------------------------------------


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
    if rollback and images is not None:
        raise DeployError(_ROLLBACK_WITH_IMAGES)
    current, generation = _read_record(cloud, env, agent)

    if rollback:
        images = _rollback_target(current, agent, env)
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
    _refuse_stale(
        current, labels.get(LABEL_CREATED), agent, env, rollback=rollback, force=force
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

    def update() -> None:
        cloud.set_job_images(env.project_id, REGION, job_name, images)
        after = cloud.get_job(env.project_id, REGION, job_name)
        running = {
            c["name"]: c["image"] for c in after["template"]["template"]["containers"]
        }
        if running != images:
            raise DeployError(
                f"{job_name} reports {running} after the update, not {images}"
            )

    return _ship(
        cloud,
        env,
        agent,
        images=images,
        config_hash=image_hash,
        runtime=applied["runtime"],
        labels=labels,
        current=current,
        generation=generation,
        rollback=rollback,
        dry_run=dry_run,
        actor=actor,
        update=update,
    )


# --- The API and UI ----------------------------------------------------------


def deploy_service(
    cloud: Cloud,
    service: str,
    env: Environment,
    image: str | None = None,
    *,
    rollback: bool = False,
    force: bool = False,
    dry_run: bool = False,
    actor: str = "unknown",
) -> Result:
    """Put ``image`` on ``service`` in ``env``, or roll back to the last deployment.

    Only the image changes; everything else about the service is Terraform's.
    A new revision is rolled out, and if it never becomes ready Cloud Run keeps
    serving the old one.
    """
    if service not in SERVICES:
        raise DeployError(
            f"{service} is not a service; services are {', '.join(SERVICES)}"
        )
    if rollback and image is not None:
        raise DeployError(_ROLLBACK_WITH_IMAGES)
    current, generation = _read_record(cloud, env, service)

    name = env.service_name(service)
    containers = [
        c["name"]
        for c in cloud.get_service(env.project_id, REGION, name)["template"][
            "containers"
        ]
    ]
    if len(containers) != 1:
        raise DeployError(
            f"{name} runs {len(containers)} containers; the deploy script handles one"
        )

    if rollback:
        images = _rollback_target(current, service, env)
    elif image:
        images = {containers[0]: image}
    else:
        raise DeployError("an image is required")
    images = {container: require_digest(ref) for container, ref in images.items()}
    if set(images) != set(containers):
        raise DeployError(
            f"{name} runs container {containers[0]}, but images were given for {sorted(images)}"
        )

    labels = cloud.image_labels(images[containers[0]])
    if current and current.get("images") == images:
        return Result(service, env.name, "unchanged", images, None, current)
    _refuse_stale(
        current, labels.get(LABEL_CREATED), service, env, rollback=rollback, force=force
    )

    def update() -> None:
        cloud.set_service_images(env.project_id, REGION, name, images)
        after = cloud.get_service(env.project_id, REGION, name)
        running = {c["name"]: c["image"] for c in after["template"]["containers"]}
        if running != images:
            raise DeployError(
                f"{name} reports {running} after the update, not {images}"
            )
        if after.get("latestReadyRevision") != after.get("latestCreatedRevision"):
            raise DeployError(
                f"the new revision of {name} ({after.get('latestCreatedRevision')}) isn't ready; "
                f"Cloud Run keeps serving {after.get('latestReadyRevision')}"
            )

    return _ship(
        cloud,
        env,
        service,
        images=images,
        config_hash=None,
        runtime="cloud_run_service",
        labels=labels,
        current=current,
        generation=generation,
        rollback=rollback,
        dry_run=dry_run,
        actor=actor,
        update=update,
    )


# --- Either --------------------------------------------------------------------


def promote(
    cloud: Cloud, name: str, source: Environment, target: Environment, **kwargs: Any
) -> Result:
    """Deploy to ``target`` exactly the images ``name`` runs in ``source``."""
    record, _ = _read_record(cloud, source, name)
    if record is None:
        raise DeployError(f"{name} has never been deployed to {source.name}")
    images = dict(record["images"])
    if name in SERVICES:
        return deploy_service(
            cloud, name, target, next(iter(images.values())), **kwargs
        )
    return deploy_agent(cloud, name, target, images, **kwargs)


def promote_agent(
    cloud: Cloud, agent: str, source: Environment, target: Environment, **kwargs: Any
) -> Result:
    """Deploy to ``target`` exactly the images ``agent`` runs in ``source``."""
    return promote(cloud, agent, source, target, **kwargs)


# --- Builds --------------------------------------------------------------------


def older_builds(cloud: Cloud, project: str, location: str, build_id: str) -> list[str]:
    """Return the builds of this build's trigger that started earlier and are still going.

    Deploys of one agent or service aren't serialised, and two at once can
    leave it running one image while the record names the other. So a build
    stops when an older one of its trigger is still going; rerunning the
    trigger once that finishes deploys the newer commit.
    """
    build = cloud.get_build(project, location, build_id)
    trigger = build.get("buildTriggerId")
    if not trigger:  # started by hand, not by a trigger
        return []
    created = datetime.fromisoformat(build["createTime"])
    return sorted(
        b["id"]
        for b in cloud.ongoing_builds(project, location, trigger)
        if b["id"] != build_id and datetime.fromisoformat(b["createTime"]) < created
    )


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

    @staticmethod
    def _run_url(project: str, region: str, kind: str, name: str) -> str:
        return f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/{kind}/{name}"

    def _patch_and_wait(self, url: str, body: dict[str, Any], what: str) -> None:
        operation = self._check(
            self.session.patch(url, json=body), f"updating {what}"
        ).json()
        deadline = time.monotonic() + self.timeout_seconds
        while not operation.get("done"):
            if time.monotonic() > deadline:
                raise DeployError(
                    f"updating {what} didn't finish in {self.timeout_seconds:.0f}s ({operation['name']})"
                )
            time.sleep(self.poll_seconds)
            operation = self._check(
                self.session.get(f"https://run.googleapis.com/v2/{operation['name']}"),
                f"waiting on {what}",
            ).json()
        if "error" in operation:
            raise DeployError(f"updating {what} failed: {operation['error']}")

    def get_job(self, project: str, region: str, name: str) -> dict[str, Any]:
        return self._check(
            self.session.get(self._run_url(project, region, "jobs", name)),
            f"Cloud Run Job {name}",
        ).json()

    def set_job_images(
        self, project: str, region: str, name: str, images: dict[str, str]
    ) -> None:
        job = self.get_job(project, region, name)
        for container in job["template"]["template"]["containers"]:
            container["image"] = images[container["name"]]
        self._patch_and_wait(self._run_url(project, region, "jobs", name), job, name)

    def get_service(self, project: str, region: str, name: str) -> dict[str, Any]:
        return self._check(
            self.session.get(self._run_url(project, region, "services", name)),
            f"Cloud Run service {name}",
        ).json()

    def set_service_images(
        self, project: str, region: str, name: str, images: dict[str, str]
    ) -> None:
        service = self.get_service(project, region, name)
        for container in service["template"]["containers"]:
            container["image"] = images[container["name"]]
        self._patch_and_wait(
            self._run_url(project, region, "services", name), service, name
        )

    @staticmethod
    def _builds_url(project: str, location: str) -> str:
        return f"https://cloudbuild.googleapis.com/v1/projects/{project}/locations/{location}/builds"

    def get_build(self, project: str, location: str, build_id: str) -> dict[str, Any]:
        return self._check(
            self.session.get(f"{self._builds_url(project, location)}/{build_id}"),
            f"build {build_id}",
        ).json()

    def ongoing_builds(
        self, project: str, location: str, trigger_id: str
    ) -> list[dict[str, Any]]:
        params = {
            "filter": f'trigger_id="{trigger_id}" AND (status="QUEUED" OR status="WORKING")'
        }
        builds: list[dict[str, Any]] = []
        while True:
            page = self._check(
                self.session.get(self._builds_url(project, location), params=params),
                f"builds of trigger {trigger_id}",
            ).json()
            builds += page.get("builds", [])
            if not page.get("nextPageToken"):
                return builds
            params["pageToken"] = page["nextPageToken"]
