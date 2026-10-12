import json
from typing import Any

import pytest
from hackbot_deploy import cli, deploy
from hackbot_deploy.deploy import (
    ENVIRONMENTS,
    DeployError,
    StoredObject,
    WaitingForInfra,
    deploy_agent,
    deploy_service,
    promote,
)

DEV, STAGE = ENVIRONMENTS["dev"], ENVIRONMENTS["stage"]
REPO = "us-docker.pkg.dev/moz-fx-hackbot-prod/hackbot-prod"


def image(name: str, n: int) -> str:
    return f"{REPO}/{name}@sha256:{n:064x}"


class FakeCloud:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], StoredObject] = {}
        self.labels: dict[str, dict[str, str]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.services: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple[str, dict[str, str]]] = []
        self.revisions_fail = False
        self.builds: list[dict[str, Any]] = []

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

    def service(self, env: deploy.Environment, service: str) -> None:
        self.services[env.service_name(service)] = {
            "template": {"containers": [{"name": service, "image": "placeholder"}]},
            "latestReadyRevision": "r1",
            "latestCreatedRevision": "r1",
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

    def get_service(self, project: str, region: str, name: str) -> dict[str, Any]:
        return json.loads(json.dumps(self.services[name]))

    def set_service_images(
        self, project: str, region: str, name: str, images: dict[str, str]
    ) -> None:
        svc = self.services[name]
        for container in svc["template"]["containers"]:
            container["image"] = images[container["name"]]
        n = int(svc["latestCreatedRevision"][1:]) + 1
        svc["latestCreatedRevision"] = f"r{n}"
        if not self.revisions_fail:
            svc["latestReadyRevision"] = f"r{n}"
        self.updates.append((name, images))

    def get_build(self, project: str, location: str, build_id: str) -> dict[str, Any]:
        return next(b for b in self.builds if b["id"] == build_id)

    def ongoing_builds(
        self, project: str, location: str, trigger_id: str
    ) -> list[dict[str, Any]]:
        return [
            b
            for b in self.builds
            if b.get("buildTriggerId") == trigger_id
            and b["status"] in ("QUEUED", "WORKING")
        ]


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


def test_redeploying_repairs_a_job_that_drifted_from_the_record(
    cloud: FakeCloud,
) -> None:
    ship(cloud, 1)
    ship(cloud, 2)
    # Terraform recreated the Job on its placeholder image.
    cloud.job(DEV, "bug-fix", ["agent", "broker"], label="h2")
    result = ship(cloud, 2)
    assert result.action == "deployed"
    job = cloud.jobs[DEV.job_name("bug-fix")]["template"]["template"]
    assert {c["image"] for c in job["containers"]} == set(result.images.values())
    # Not a new deployment: the history still steps back to 1, once.
    history = cloud.record(DEV, "bug-fix")["history"]
    assert [h["images"]["agent"] for h in history] == [image("bug-fix-agent", 1)]


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


def test_rollback_keeps_earlier_deployments_of_the_same_images(
    cloud: FakeCloud,
) -> None:
    # A, B, C, B again (forced), D: the history is B, C, B, A.
    a, b, c = (
        ship(cloud, n, created=f"2026-10-09T1{n}:00:00Z").images for n in (1, 2, 3)
    )
    ship(cloud, 2, created="2026-10-09T12:00:00Z", force=True)
    ship(cloud, 4, created="2026-10-09T14:00:00Z")

    steps = [
        deploy_agent(cloud, "bug-fix", DEV, rollback=True).images for _ in range(4)
    ]
    assert steps == [b, c, b, a]


def test_rollback_refuses_explicit_images(cloud: FakeCloud) -> None:
    first = ship(cloud, 1).images
    ship(cloud, 2, created="2026-10-09T11:00:00Z")

    with pytest.raises(DeployError, match="takes no images"):
        deploy_agent(cloud, "bug-fix", DEV, first, rollback=True)
    assert len(cloud.updates) == 2


@pytest.mark.parametrize(
    "flags",
    [
        ["--image", image("hackbot-api", 1)],
        ["--agent-image", image("bug-fix", 1)],
        ["--broker-image", image("bug-fix-broker", 1)],
    ],
)
def test_rollback_cli_takes_no_images(flags: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["rollback", "bug-fix", "--env", "dev", *flags])
    assert exc.value.code == 2


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

    result = promote(cloud, "bug-fix", DEV, STAGE, actor="test")
    assert result.images == images
    assert cloud.updates[-1] == ("hackbot-agent-bug-fix-stage", images)


def test_promote_waits_for_infra_in_the_target(cloud: FakeCloud) -> None:
    ship(cloud, 1)
    cloud.apply(STAGE, "bug-fix", "h1")
    cloud.job(STAGE, "bug-fix", ["agent", "broker"], label="h1")
    with pytest.raises(WaitingForInfra):
        promote(cloud, "bug-fix", DEV, STAGE)


def test_environment_names_match_the_terraform() -> None:
    assert ENVIRONMENTS["dev"].job_name("bug-fix") == "hackbot-agent-bug-fix-dev"
    assert ENVIRONMENTS["prod"].job_name("bug-fix") == "hackbot-agent-bug-fix"
    assert (
        ENVIRONMENTS["stage"].registry_bucket == "moz-fx-hackbot-nonprod-registry-stage"
    )
    assert ENVIRONMENTS["prod"].registry_bucket == "moz-fx-hackbot-prod-registry"
    assert ENVIRONMENTS["stage"].service_name("hackbot-api") == "hackbot-api-stage"
    assert ENVIRONMENTS["prod"].service_name("hackbot-ui") == "hackbot-ui"


# --- Services ----------------------------------------------------------------


@pytest.fixture
def services() -> FakeCloud:
    fake = FakeCloud()
    for env in (DEV, STAGE):
        fake.service(env, "hackbot-api")
    return fake


def api_image(cloud: FakeCloud, n: int, created: str = "2026-10-09T10:00:00Z") -> str:
    ref = f"{REPO}/hackbot-api@sha256:{n:064x}"
    # Service images carry no config hash: services have no gate.
    cloud.labels[ref] = {deploy.LABEL_CREATED: created, deploy.LABEL_REVISION: "abc123"}
    return ref


def test_service_deploys_without_a_gate(services: FakeCloud) -> None:
    result = deploy_service(
        services, "hackbot-api", DEV, api_image(services, 1), actor="test"
    )

    assert result.action == "deployed"
    assert result.config_hash is None
    assert services.updates == [
        ("hackbot-api-dev", {"hackbot-api": api_image(services, 1)})
    ]
    record = services.record(DEV, "hackbot-api")
    assert (record["runtime"], record["config_hash"]) == ("cloud_run_service", None)


def test_service_refuses_tags(services: FakeCloud) -> None:
    with pytest.raises(DeployError, match="digest"):
        deploy_service(services, "hackbot-api", DEV, f"{REPO}/hackbot-api:latest")


def test_only_known_services_are_services(services: FakeCloud) -> None:
    with pytest.raises(DeployError, match="not a service"):
        deploy_service(services, "bug-fix", DEV, api_image(services, 1))


def test_a_revision_that_never_becomes_ready_fails_and_is_not_recorded(
    services: FakeCloud,
) -> None:
    services.revisions_fail = True
    with pytest.raises(DeployError, match="keeps serving r1"):
        deploy_service(services, "hackbot-api", DEV, api_image(services, 1))
    assert (DEV.registry_bucket, "deployments/hackbot-api.json") not in services.objects


def test_service_redeploy_is_a_no_op_and_stale_builds_are_refused(
    services: FakeCloud,
) -> None:
    deploy_service(
        services, "hackbot-api", DEV, api_image(services, 2, "2026-10-09T12:00:00Z")
    )
    assert (
        deploy_service(
            services, "hackbot-api", DEV, api_image(services, 2, "2026-10-09T12:00:00Z")
        ).action
        == "unchanged"
    )
    with pytest.raises(DeployError, match="--force"):
        deploy_service(
            services, "hackbot-api", DEV, api_image(services, 1, "2026-10-09T11:00:00Z")
        )


def test_service_redeploy_repairs_drift(services: FakeCloud) -> None:
    ref = api_image(services, 1)
    deploy_service(services, "hackbot-api", DEV, ref)
    services.service(DEV, "hackbot-api")  # recreated on its placeholder image
    assert deploy_service(services, "hackbot-api", DEV, ref).action == "deployed"
    svc = services.services[DEV.service_name("hackbot-api")]
    assert svc["template"]["containers"][0]["image"] == ref
    assert services.record(DEV, "hackbot-api")["history"] == []


def test_service_rollback_goes_back_one_deployment_at_a_time(
    services: FakeCloud,
) -> None:
    first, second = (
        deploy_service(
            services,
            "hackbot-api",
            DEV,
            api_image(services, n, f"2026-10-09T1{n}:00:00Z"),
        ).images
        for n in (1, 2)
    )
    deploy_service(
        services, "hackbot-api", DEV, api_image(services, 3, "2026-10-09T13:00:00Z")
    )

    assert deploy_service(services, "hackbot-api", DEV, rollback=True).images == second
    assert deploy_service(services, "hackbot-api", DEV, rollback=True).images == first


def test_service_rollback_refuses_an_explicit_image(services: FakeCloud) -> None:
    first = api_image(services, 1, "2026-10-09T11:00:00Z")
    deploy_service(services, "hackbot-api", DEV, first)
    deploy_service(
        services, "hackbot-api", DEV, api_image(services, 2, "2026-10-09T12:00:00Z")
    )

    with pytest.raises(DeployError, match="takes no images"):
        deploy_service(services, "hackbot-api", DEV, first, rollback=True)
    assert len(services.updates) == 2


def test_promote_dispatches_services(services: FakeCloud) -> None:
    images = deploy_service(services, "hackbot-api", DEV, api_image(services, 1)).images
    result = promote(services, "hackbot-api", DEV, STAGE, actor="test")
    assert result.images == images
    assert services.updates[-1] == ("hackbot-api-stage", images)


def build(id: str, created: str, status: str = "WORKING", trigger: str | None = "t1"):
    return {
        "id": id,
        "createTime": created,
        "status": status,
        **({"buildTriggerId": trigger} if trigger else {}),
    }


def test_a_build_stops_while_an_older_one_of_its_trigger_runs() -> None:
    fake = FakeCloud()
    fake.builds = [
        build("older", "2026-10-10T13:25:39.093624Z", status="QUEUED"),
        build("this", "2026-10-10T13:25:40Z"),
        build("newer", "2026-10-10T13:26:00Z"),
        build("finished", "2026-10-10T13:00:00Z", status="SUCCESS"),
        build("other-trigger", "2026-10-10T13:00:00Z", trigger="t2"),
    ]
    assert deploy.older_builds(fake, "p", "us-west1", "this") == ["older"]
    assert deploy.older_builds(fake, "p", "us-west1", "older") == []


def test_a_build_started_by_hand_is_never_stopped() -> None:
    fake = FakeCloud()
    fake.builds = [
        build("older", "2026-10-10T13:00:00Z", trigger=None),
        build("this", "2026-10-10T13:25:40Z", trigger=None),
    ]
    assert deploy.older_builds(fake, "p", "us-west1", "this") == []


D = "sha256:" + "0" * 64


@pytest.mark.parametrize(
    "ref",
    [
        f"evil.example#.us-docker.pkg.dev/x@{D}",
        f"us-west1-docker.pkg.dev@evil.example/x@{D}",
        f"us-docker.pkg.dev:443@evil.example/x@{D}",
        f"user@us-docker.pkg.dev/x@{D}",
        f"us-docker.pkg.dev/x@{D}@evil.example",
        f"us-docker.pkg.dev/x?y@{D}",
        "us-docker.pkg.dev/x@sha256:abc",
    ],
)
def test_malformed_image_references_are_refused(ref: str) -> None:
    with pytest.raises(DeployError, match="not a valid image reference"):
        deploy.split_image(ref)


def test_image_references_split_into_host_path_and_digest() -> None:
    assert deploy.split_image(f"us-west1-docker.pkg.dev/p/r/agent@{D}") == (
        "us-west1-docker.pkg.dev",
        "p/r/agent",
        D,
    )


def test_a_job_without_the_config_hash_label_is_an_incomplete_apply(
    cloud: FakeCloud,
) -> None:
    cloud.job(DEV, "bug-fix", ["agent", "broker"])
    with pytest.raises(DeployError, match="no config hash"):
        ship(cloud, 1)
    assert cloud.updates == []
