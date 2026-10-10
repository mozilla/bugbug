import json
from typing import Any

import pytest
from hackbot_deploy import deploy
from hackbot_deploy.deploy import (
    ENVIRONMENTS,
    DeployError,
    StoredObject,
    WaitingForInfra,
    deploy_agent,
    promote_agent,
)

DEV, STAGE = ENVIRONMENTS["dev"], ENVIRONMENTS["stage"]
REPO = "us-west1-docker.pkg.dev/moz-fx-hackbot-prod/hackbot"


def image(name: str, n: int) -> str:
    return f"{REPO}/{name}@sha256:{n:064x}"


class FakeCloud:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], StoredObject] = {}
        self.labels: dict[str, dict[str, str]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple[str, dict[str, str]]] = []

    # --- setup helpers ---
    def apply(
        self,
        env: deploy.Environment,
        agent: str,
        applied: str,
        compatible: list[str] | None = None,
    ) -> None:
        body = {
            "agents": {
                agent: {
                    "runtime": "cloud_run_job",
                    "applied": applied,
                    "compatible": compatible or [applied],
                }
            }
        }
        self.put(env.registry_bucket, "applied.json", body)

    def job(
        self,
        env: deploy.Environment,
        agent: str,
        containers: list[str],
        label: str | None = None,
    ) -> None:
        self.jobs[env.job_name(agent)] = {
            "labels": {deploy.JOB_LABEL_CONFIG_HASH: label} if label else {},
            "template": {
                "template": {
                    "containers": [
                        {"name": c, "image": "placeholder"} for c in containers
                    ]
                }
            },
        }

    def build(
        self, ref: str, config_hash: str, created: str = "2026-10-09T10:00:00Z"
    ) -> str:
        self.labels[ref] = {
            deploy.LABEL_CONFIG_HASH: config_hash,
            deploy.LABEL_CREATED: created,
            deploy.LABEL_REVISION: "abc123",
        }
        return ref

    def put(self, bucket: str, name: str, body: dict) -> None:
        current = self.objects.get((bucket, name))
        self.objects[(bucket, name)] = StoredObject(
            json.dumps(body).encode(), (current.generation if current else 0) + 1
        )

    def record(self, env: deploy.Environment, agent: str) -> dict:
        return json.loads(
            self.objects[(env.registry_bucket, f"deployments/{agent}.json")].data
        )

    # --- the Cloud protocol ---
    def read_object(self, bucket: str, name: str) -> StoredObject | None:
        return self.objects.get((bucket, name))

    def write_object(
        self, bucket: str, name: str, data: bytes, if_generation_match: int
    ) -> None:
        current = self.objects.get((bucket, name))
        if (current.generation if current else 0) != if_generation_match:
            raise DeployError("precondition failed")
        self.objects[(bucket, name)] = StoredObject(data, if_generation_match + 1)

    def image_labels(self, ref: str) -> dict[str, str]:
        return self.labels.get(ref, {})

    def get_job(self, project: str, region: str, name: str) -> dict[str, Any]:
        return json.loads(json.dumps(self.jobs[name]))

    def set_job_images(
        self, project: str, region: str, name: str, images: dict[str, str]
    ) -> None:
        for container in self.jobs[name]["template"]["template"]["containers"]:
            container["image"] = images[container["name"]]
        self.updates.append((name, images))


@pytest.fixture
def cloud() -> FakeCloud:
    fake = FakeCloud()
    fake.apply(DEV, "bug-fix", "h2", ["h1", "h2"])
    fake.job(DEV, "bug-fix", ["agent", "broker"], label="h2")
    return fake


def ship(
    cloud: FakeCloud,
    n: int,
    config_hash: str = "h2",
    created: str = "2026-10-09T10:00:00Z",
    **kw: Any,
):
    images = {
        "agent": cloud.build(image("bug-fix-agent", n), config_hash, created),
        "broker": cloud.build(image("bug-fix-broker", n), config_hash, created),
    }
    return deploy_agent(cloud, "bug-fix", DEV, images, actor="test", **kw)


def test_deploys_and_records(cloud: FakeCloud) -> None:
    result = ship(cloud, 1)

    assert result.action == "deployed"
    assert cloud.updates == [("hackbot-agent-bug-fix-dev", result.images)]
    record = cloud.record(DEV, "bug-fix")
    assert record["images"] == result.images
    assert (record["config_hash"], record["revision"], record["history"]) == (
        "h2",
        "abc123",
        [],
    )


def test_waits_for_infra_when_the_shape_is_not_applied(cloud: FakeCloud) -> None:
    with pytest.raises(WaitingForInfra, match="h3"):
        ship(cloud, 1, config_hash="h3")
    assert cloud.updates == []
    assert (DEV.registry_bucket, "deployments/bug-fix.json") not in cloud.objects


def test_waits_for_infra_when_the_agent_is_not_applied(cloud: FakeCloud) -> None:
    with pytest.raises(WaitingForInfra):
        deploy_agent(
            cloud, "new-agent", DEV, {"agent": cloud.build(image("new-agent", 1), "h1")}
        )


def test_refuses_tags(cloud: FakeCloud) -> None:
    with pytest.raises(DeployError, match="digest"):
        deploy_agent(cloud, "bug-fix", DEV, {"agent": f"{REPO}/bug-fix-agent:latest"})


def test_refuses_images_built_for_different_shapes(cloud: FakeCloud) -> None:
    images = {
        "agent": cloud.build(image("bug-fix-agent", 1), "h2"),
        "broker": cloud.build(image("bug-fix-broker", 1), "h1"),
    }
    with pytest.raises(DeployError, match="different shapes"):
        deploy_agent(cloud, "bug-fix", DEV, images)


def test_refuses_images_without_the_label(cloud: FakeCloud) -> None:
    with pytest.raises(DeployError, match="label"):
        deploy_agent(
            cloud,
            "bug-fix",
            DEV,
            {"agent": image("bug-fix-agent", 1), "broker": image("bug-fix-broker", 1)},
        )


def test_the_container_set_must_match_the_job(cloud: FakeCloud) -> None:
    with pytest.raises(DeployError, match="containers"):
        deploy_agent(
            cloud,
            "bug-fix",
            DEV,
            {"agent": cloud.build(image("bug-fix-agent", 1), "h2")},
        )


def test_an_incomplete_apply_is_caught(cloud: FakeCloud) -> None:
    cloud.job(DEV, "bug-fix", ["agent", "broker"], label="h1")
    with pytest.raises(DeployError, match="incomplete"):
        ship(cloud, 1)


def test_redeploying_the_same_images_is_a_no_op(cloud: FakeCloud) -> None:
    ship(cloud, 1)
    assert ship(cloud, 1).action == "unchanged"
    assert len(cloud.updates) == 1


def test_an_older_build_does_not_replace_a_newer_one(cloud: FakeCloud) -> None:
    ship(cloud, 2, created="2026-10-09T12:00:00Z")
    with pytest.raises(DeployError, match="--force"):
        ship(cloud, 1, created="2026-10-09T11:00:00Z")
    assert (
        ship(cloud, 1, created="2026-10-09T11:00:00Z", force=True).action == "deployed"
    )


def test_rollback_goes_back_one_deployment_at_a_time(cloud: FakeCloud) -> None:
    first, second, third = (
        ship(cloud, n, created=f"2026-10-09T1{n}:00:00Z").images for n in (1, 2, 3)
    )

    assert deploy_agent(cloud, "bug-fix", DEV, rollback=True).images == second
    assert deploy_agent(cloud, "bug-fix", DEV, rollback=True).images == first
    with pytest.raises(DeployError, match="no earlier deployment"):
        deploy_agent(cloud, "bug-fix", DEV, rollback=True)
    assert cloud.record(DEV, "bug-fix")["images"] == first
    assert third not in [h["images"] for h in cloud.record(DEV, "bug-fix")["history"]]


def test_rollback_onto_a_compatible_older_shape(cloud: FakeCloud) -> None:
    cloud.apply(DEV, "bug-fix", "h1")
    cloud.job(DEV, "bug-fix", ["agent", "broker"], label="h1")
    old = ship(cloud, 1, config_hash="h1").images
    # An expand moves the applied shape to h2; h1 stays compatible.
    cloud.apply(DEV, "bug-fix", "h2", ["h1", "h2"])
    cloud.job(DEV, "bug-fix", ["agent", "broker"], label="h2")
    ship(cloud, 2, config_hash="h2", created="2026-10-09T11:00:00Z")

    assert deploy_agent(cloud, "bug-fix", DEV, rollback=True).images == old


def test_rollback_is_refused_past_a_contract(cloud: FakeCloud) -> None:
    cloud.apply(DEV, "bug-fix", "h1")
    cloud.job(DEV, "bug-fix", ["agent", "broker"], label="h1")
    ship(cloud, 1, config_hash="h1")
    # A contract resets compatible to the new shape alone.
    cloud.apply(DEV, "bug-fix", "h2", ["h2"])
    cloud.job(DEV, "bug-fix", ["agent", "broker"], label="h2")
    ship(cloud, 2, config_hash="h2", created="2026-10-09T11:00:00Z")

    with pytest.raises(DeployError, match="contract"):
        deploy_agent(cloud, "bug-fix", DEV, rollback=True)


def test_dry_run_changes_nothing(cloud: FakeCloud) -> None:
    result = ship(cloud, 1, dry_run=True)
    assert result.action == "would be deployed"
    assert cloud.updates == []
    assert (DEV.registry_bucket, "deployments/bug-fix.json") not in cloud.objects


def test_a_concurrent_deploy_is_detected(cloud: FakeCloud) -> None:
    ship(cloud, 1)
    real_read = cloud.read_object

    def stale_read(bucket: str, name: str) -> StoredObject | None:
        stored = real_read(bucket, name)
        if name.startswith("deployments/") and stored:
            return StoredObject(stored.data, stored.generation - 1)
        return stored

    cloud.read_object = stale_read  # type: ignore[method-assign]
    with pytest.raises(DeployError, match="precondition"):
        ship(cloud, 2, created="2026-10-09T11:00:00Z")


def test_promote_deploys_the_source_environment_images(cloud: FakeCloud) -> None:
    images = ship(cloud, 1).images
    cloud.apply(STAGE, "bug-fix", "h2")
    cloud.job(STAGE, "bug-fix", ["agent", "broker"], label="h2")

    result = promote_agent(cloud, "bug-fix", DEV, STAGE, actor="test")
    assert result.images == images
    assert cloud.updates[-1] == ("hackbot-agent-bug-fix-stage", images)


def test_promote_waits_for_infra_in_the_target(cloud: FakeCloud) -> None:
    ship(cloud, 1)
    cloud.apply(STAGE, "bug-fix", "h1")
    cloud.job(STAGE, "bug-fix", ["agent", "broker"], label="h1")
    with pytest.raises(WaitingForInfra):
        promote_agent(cloud, "bug-fix", DEV, STAGE)


def test_environment_names_match_the_terraform() -> None:
    assert ENVIRONMENTS["dev"].job_name("bug-fix") == "hackbot-agent-bug-fix-dev"
    assert ENVIRONMENTS["prod"].job_name("bug-fix") == "hackbot-agent-bug-fix"
    assert (
        ENVIRONMENTS["stage"].registry_bucket == "moz-fx-hackbot-nonprod-registry-stage"
    )
    assert ENVIRONMENTS["prod"].registry_bucket == "moz-fx-hackbot-prod-registry"
