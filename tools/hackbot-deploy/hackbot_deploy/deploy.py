"""Ship images onto hackbot's infrastructure: agents and the API and UI.

Terraform owns the infrastructure; this owns which image runs on it.
Deploying, promoting and rolling back are all the same operation: set the
images something runs in one environment, then record that in the
environment's registry bucket (``deployments/<id>.json``).

Everything deployable is named by where it lives in the repository, which also
says what it is: ``agents/<name>`` (a Cloud Run Job, from an agent's
``[deploy]`` table) or ``services/<name>`` (a Cloud Run service, with its own
``cloudbuild.yaml``).

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

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

REGION = "us-west1"
LABEL_CONFIG_HASH = "hackbot.config_hash"
LABEL_REVISION = "org.opencontainers.image.revision"
LABEL_CREATED = "org.opencontainers.image.created"
JOB_LABEL_CONFIG_HASH = "hackbot-config-hash"
HISTORY_LIMIT = 10
# A registry image pinned by digest. The host is plain DNS labels: no "#",
# "@", ":" or "?", any of which could make a URL built from it reach another
# host. Which hosts are trusted is the caller's call (see gcp.py).
_IMAGE = re.compile(
    r"(?P<host>[a-z0-9]+(?:[.-][a-z0-9]+)*)"
    r"/(?P<path>[a-z0-9._-]+(?:/[a-z0-9._-]+)*)"
    r"@(?P<digest>sha256:[0-9a-f]{64})"
)

AGENTS = "agents"
SERVICES = "services"

# The GitHub environment a prod promotion of each kind waits for approval in.
PROD_APPROVAL = {AGENTS: "hackbot-prod-agents", SERVICES: "hackbot-prod-services"}


def split_id(deployable: str) -> tuple[str, str]:
    """Return a deployable's kind and name: ``agents/bug-fix`` -> (agents, bug-fix)."""
    kind, _, name = deployable.partition("/")
    if kind not in (AGENTS, SERVICES) or not name or "/" in name:
        raise DeployError(
            f"{deployable}: not a deployable; expected {AGENTS}/<name> or {SERVICES}/<name>"
        )
    return kind, name


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
    """What the deploy needs from GCP. The real one is :class:`hackbot_deploy.gcp.GoogleCloud`."""

    def read_object(self, bucket: str, name: str) -> StoredObject | None: ...

    def write_object(
        self, bucket: str, name: str, data: bytes, if_generation_match: int
    ) -> None:
        """Write, but only if the object is still at that generation (0: absent)."""

    def image_labels(self, image: str) -> dict[str, str]: ...

    def image_digest(self, image: str) -> str:
        """Pin a tagged image (host/path:tag) to the digest the tag names now."""

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


def split_image(image: str) -> tuple[str, str, str]:
    """Return an image's registry host, repository path and digest.

    Refuses anything not pinned by digest, or not a well-formed reference.
    """
    if "@sha256:" not in image:
        raise DeployError(
            f"{image}: images must be pinned by digest (…@sha256:…), not by tag"
        )
    match = _IMAGE.fullmatch(image)
    if match is None:
        raise DeployError(f"{image}: not a valid image reference (host/path@sha256:…)")
    return match["host"], match["path"], match["digest"]


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
        # The target (always the latest entry) leaves the history; the
        # deployment being rolled back doesn't join it, so rolling back twice
        # goes back two deployments.
        return (current or {}).get("history", [])[1:]
    if not current:
        return []
    if current.get("images") == images:
        # Putting the recorded images back on a resource that drifted from
        # them isn't a new deployment.
        return current.get("history", [])
    return [_summary(current)] + current.get("history", [])


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


def _adopt_terraform_deploy(
    cloud: Cloud,
    env: Environment,
    key: str,
    applied: dict[str, Any],
    running: dict[str, str],
    current: dict[str, Any] | None,
    generation: int,
    *,
    dry_run: bool,
) -> tuple[dict[str, Any] | None, int]:
    """Record images Terraform put on, if it did, as the current deployment.

    An apply that moves an agent to a new shape also puts on the images pinned
    for it (``images`` in applied.json), without touching the record. Left
    alone, the record would name the old images, and a rollback would step
    back past them.
    """
    pinned = applied.get("images")
    if (
        not pinned
        or running != pinned
        or (current and current.get("images") == running)
    ):
        return current, generation
    labels = cloud.image_labels(running["agent"])
    record = {
        "name": key,
        "environment": env.name,
        "runtime": applied["runtime"],
        "images": running,
        "config_hash": applied["applied"],
        "revision": labels.get(LABEL_REVISION),
        "created": labels.get(LABEL_CREATED),
        "deployed_at": _now(),
        "deployed_by": "terraform",
        "history": _history(current, running, rollback=False)[:HISTORY_LIMIT],
    }
    if dry_run:
        return record, generation
    cloud.write_object(
        env.registry_bucket,
        f"deployments/{key}.json",
        (json.dumps(record, indent=2) + "\n").encode(),
        if_generation_match=generation,
    )
    return _read_record(cloud, env, key)


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
    key = f"{AGENTS}/{agent}"
    current, generation = _read_record(cloud, env, key)
    applied = _applied(cloud, env, agent)

    job_name = env.job_name(agent)
    job = cloud.get_job(env.project_id, REGION, job_name)
    job_hash = job.get("labels", {}).get(JOB_LABEL_CONFIG_HASH)
    if job_hash != applied["applied"]:
        raise DeployError(
            f"{job_name} is labelled {job_hash or 'with no config hash'} but applied.json says {applied['applied']}; "
            "a Terraform apply looks incomplete"
        )
    running = {c["name"]: c["image"] for c in job["template"]["template"]["containers"]}
    current, generation = _adopt_terraform_deploy(
        cloud, env, key, applied, running, current, generation, dry_run=dry_run
    )

    if rollback:
        images = _rollback_target(current, key, env)
    if not images or "agent" not in images:
        raise DeployError("an agent image is required")
    for ref in images.values():
        split_image(ref)

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

    if set(running) != set(images):
        raise DeployError(
            f"{job_name} runs containers {sorted(running)}, but images were given for {sorted(images)}"
        )
    # Compared with what the Job runs, not just the record: Terraform can
    # recreate a Job on its placeholder image, and a redeploy must repair that.
    if current and current.get("images") == images == running:
        return Result(key, env.name, "unchanged", images, image_hash, current)
    _refuse_stale(
        current, labels.get(LABEL_CREATED), agent, env, rollback=rollback, force=force
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
        key,
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
    if rollback and image is not None:
        raise DeployError(_ROLLBACK_WITH_IMAGES)
    key = f"{SERVICES}/{service}"
    current, generation = _read_record(cloud, env, key)

    name = env.service_name(service)
    running = {
        c["name"]: c["image"]
        for c in cloud.get_service(env.project_id, REGION, name)["template"][
            "containers"
        ]
    }
    containers = list(running)
    if len(containers) != 1:
        raise DeployError(
            f"{name} runs {len(containers)} containers; the deploy script handles one"
        )

    if rollback:
        images = _rollback_target(current, key, env)
    elif image:
        images = {containers[0]: image}
    else:
        raise DeployError("an image is required")
    for ref in images.values():
        split_image(ref)
    if set(images) != set(containers):
        raise DeployError(
            f"{name} runs container {containers[0]}, but images were given for {sorted(images)}"
        )

    labels = cloud.image_labels(images[containers[0]])
    if current and current.get("images") == images == running:
        return Result(key, env.name, "unchanged", images, None, current)
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
        key,
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


def promotion(
    cloud: Cloud, deployable: str, source: Environment, target: Environment
) -> dict[str, Any] | None:
    """Say what promoting ``deployable`` from ``source`` to ``target`` would put on.

    None when there is nothing to promote: ``source`` has no deployment, or
    ``target`` already runs the same images. ``environment`` is the GitHub
    environment a prod promotion of its kind waits for approval in.
    """
    kind, _ = split_id(deployable)
    record, _ = _read_record(cloud, source, deployable)
    if record is None:
        return None
    current, _ = _read_record(cloud, target, deployable)
    if current and current.get("images") == record["images"]:
        return None
    return {
        "name": deployable,
        "kind": kind,
        "environment": PROD_APPROVAL[kind],
        "images": record["images"],
        "revision": record.get("revision"),
        "created": record.get("created"),
        "deployed_by": record.get("deployed_by"),
        "replaces": _summary(current) if current else None,
    }


def promote(
    cloud: Cloud,
    deployable: str,
    source: Environment,
    target: Environment,
    *,
    expect: dict[str, str] | None = None,
    **kwargs: Any,
) -> Result:
    """Deploy to ``target`` exactly the images ``deployable`` runs in ``source``.

    With ``expect``, only if ``source`` still runs those images: a promotion
    approved after ``source`` moved on is superseded, not a deploy of
    something nobody looked at.
    """
    kind, name = split_id(deployable)
    record, _ = _read_record(cloud, source, deployable)
    if record is None:
        raise DeployError(f"{deployable} has never been deployed to {source.name}")
    images = dict(record["images"])
    if expect is not None and images != expect:
        return Result(deployable, target.name, "superseded", expect, None)
    if kind == SERVICES:
        return deploy_service(
            cloud, name, target, next(iter(images.values())), **kwargs
        )
    return deploy_agent(cloud, name, target, images, **kwargs)


# --- Builds --------------------------------------------------------------------


def built_images(
    cloud: Cloud,
    repository: str,
    agent: str,
    tag: str,
    config_hash: str,
    has_broker: bool,
) -> dict[str, str] | None:
    """Return the digests of the images a build pushed for ``agent`` as ``tag``.

    None when they were built for another shape: the agent's shape has changed
    since that commit, and the build of the newer commit pins its own images.
    """
    containers = ["agent", "broker"] if has_broker else ["agent"]
    images = {
        container: cloud.image_digest(f"{repository}/{agent}-{container}:{tag}")
        for container in containers
    }
    for image in images.values():
        if cloud.image_labels(image).get(LABEL_CONFIG_HASH) != config_hash:
            return None
    return images


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
