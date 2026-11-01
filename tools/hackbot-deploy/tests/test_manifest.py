import json
from pathlib import Path

import pytest
from hackbot_deploy import manifest
from hackbot_deploy.manifest import (
    MAX_COMPATIBLE,
    Change,
    build_manifest,
    classify,
    config_hash,
)
from hackbot_deploy.schema import DeployConfig

# The hand-written agents.json that webservices-infra started from.
FIXTURE = Path(__file__).parent / "fixtures" / "agents.json"

JOB = {"runtime": "cloud_run_job", "cpu": "6", "memory": "24Gi"}


def config(**fields: object) -> DeployConfig:
    return DeployConfig.model_validate({**JOB, **fields})


def fixture_configs() -> dict[str, DeployConfig]:
    # The [deploy] tables the fixture was written for, kept here so adding or
    # retuning an agent under agents/ doesn't break the byte-for-byte check.
    broker = {"cpu": "1", "memory": "512Mi"}
    return {
        "autowebcompat-diagnosis": config(timeout_seconds=7200, broker=broker),
        "autowebcompat-repro": config(timeout_seconds=7200, broker=broker),
        "bug-fix": config(
            timeout_seconds=7200, workspace={"size": "40Gi"}, broker=broker
        ),
        "build-repair": config(timeout_seconds=7200, broker=broker),
        "frontend-triage": config(timeout_seconds=7200, broker=broker),
        "test-plan-generator": config(timeout_seconds=7200),
        "test-repair": config(timeout_seconds=7200),
    }


def test_reproduces_the_infra_manifest_byte_for_byte() -> None:
    previous = json.loads(FIXTURE.read_text())
    generated, changes = build_manifest(fixture_configs(), previous)

    expected = FIXTURE.read_text().replace(
        '"generator": "manual"', f'"generator": "{manifest.GENERATOR}"'
    )
    assert manifest.dumps(generated) == expected
    assert set(changes.values()) == {Change.UNCHANGED}


def test_agents_without_a_deploy_table_are_left_out(tmp_path: Path) -> None:
    (tmp_path / "deployed").mkdir()
    (tmp_path / "deployed" / "hackbot.toml").write_text(
        '[deploy]\nruntime = "cloud_run_job"\ncpu = "1"\nmemory = "2Gi"\n'
    )
    (tmp_path / "in-progress").mkdir()
    (tmp_path / "in-progress" / "hackbot.toml").write_text(
        '[source]\nrepo_url = "https://example.com/x.git"\n'
    )

    assert list(manifest.load_configs(tmp_path)) == ["deployed"]


def test_an_invalid_table_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "typo").mkdir()
    path = tmp_path / "typo" / "hackbot.toml"
    path.write_text(
        '[deploy]\nruntime = "cloud_run_job"\ncpu = "1"\nmemory_gb = "2Gi"\n'
    )

    with pytest.raises(manifest.ManifestError, match=str(path)):
        manifest.load_configs(tmp_path)


def test_the_hash_includes_the_agent_name() -> None:
    entry = config().manifest_entry()
    assert config_hash("one", entry) != config_hash("other", entry)


def test_an_empty_table_hashes_like_a_missing_one() -> None:
    assert config_hash("a", config().manifest_entry()) == config_hash(
        "a", config(secret_env={}).manifest_entry()
    )


def test_the_hash_ignores_bookkeeping_and_key_order() -> None:
    entry = config(secret_env={"B": "b-secret", "A": "a-secret"}).manifest_entry()
    reordered = {
        **dict(reversed(list(entry.items()))),
        "config_hash": "x",
        "compatible": ["y"],
    }
    assert config_hash("a", entry) == config_hash("a", reordered)


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ({}, {"cpu": "8", "memory": "32Gi"}, Change.EXPAND),
        ({}, {"timeout_seconds": 3600}, Change.EXPAND),
        ({}, {"secret_env": {"NEW": "new-secret"}}, Change.EXPAND),
        (
            {"secret_env": {"A": "a-secret"}},
            {"secret_env": {"A": "a-secret", "B": "b-secret"}},
            Change.EXPAND,
        ),
        ({}, {"workspace": {"size": "40Gi"}}, Change.EXPAND),
        ({"secret_env": {"KEY": "some-secret"}}, {}, Change.CONTRACT),
        # The same variable pointed at another secret.
        (
            {"secret_env": {"KEY": "some-secret"}},
            {"secret_env": {"KEY": "other-secret"}},
            Change.CONTRACT,
        ),
        ({"workspace": {"size": "40Gi"}}, {}, Change.CONTRACT),
        # A broker either way changes the container set.
        ({}, {"broker": {}}, Change.CONTRACT),
        ({"broker": {}}, {}, Change.CONTRACT),
        (
            {"broker": {"secret_env": {"KEY": "some-secret"}}},
            {"broker": {}},
            Change.CONTRACT,
        ),
        (
            {"broker": {"secret_env": {"KEY": "some-secret"}}},
            {"broker": {"secret_env": {"KEY": "other-secret"}}},
            Change.CONTRACT,
        ),
        (
            {"broker": {}},
            {"broker": {"secret_env": {"KEY": "some-secret"}}},
            Change.EXPAND,
        ),
        ({"broker": {}}, {"broker": {"cpu": "2", "memory": "4Gi"}}, Change.EXPAND),
    ],
)
def test_classify(before: dict, after: dict, expected: Change) -> None:
    assert (
        classify(config(**before).manifest_entry(), config(**after).manifest_entry())
        is expected
    )


def test_classify_treats_a_runtime_change_as_a_contract() -> None:
    entry = config().manifest_entry()
    assert classify({**entry, "runtime": "batch"}, entry) is Change.CONTRACT


def _regenerate(previous: dict, cfg: DeployConfig) -> tuple[dict, Change]:
    generated, changes = build_manifest({"a": cfg}, previous)
    return generated["agents"]["a"], changes["a"]


def test_compatible_through_add_expand_unchanged_and_contract() -> None:
    first, _ = build_manifest({"a": config()})
    h1 = first["agents"]["a"]["config_hash"]
    assert first["agents"]["a"]["compatible"] == [h1]

    expanded, change = _regenerate(first, config(cpu="8", memory="32Gi"))
    assert change is Change.EXPAND
    assert expanded["compatible"] == [h1, expanded["config_hash"]]

    second = {**first, "agents": {"a": expanded}}
    unchanged, change = _regenerate(second, config(cpu="8", memory="32Gi"))
    assert change is Change.UNCHANGED
    assert unchanged["compatible"] == expanded["compatible"]

    contracted, change = _regenerate(second, config(cpu="8", memory="32Gi", broker={}))
    assert change is Change.CONTRACT
    assert contracted["compatible"] == [contracted["config_hash"]]


def test_compatible_is_capped() -> None:
    current, _ = build_manifest({"a": config()})
    for minutes in range(1, MAX_COMPATIBLE + 5):
        entry, _ = _regenerate(current, config(timeout_seconds=60 * minutes))
        current = {**current, "agents": {"a": entry}}
    compatible = current["agents"]["a"]["compatible"]
    assert len(compatible) == MAX_COMPATIBLE
    assert compatible[-1] == current["agents"]["a"]["config_hash"]


def test_removed_agents_are_reported() -> None:
    previous, _ = build_manifest({"a": config(), "b": config()})
    generated, changes = build_manifest({"a": config()}, previous)
    assert list(generated["agents"]) == ["a"]
    assert changes["b"] is Change.REMOVED


def test_a_previous_manifest_of_another_schema_version_is_refused() -> None:
    with pytest.raises(manifest.ManifestError, match="schema_version"):
        build_manifest({"a": config()}, {"schema_version": 2, "agents": {}})


def test_dumps_escapes_non_ascii_like_the_infra_hook() -> None:
    assert "h\\u00e9llo" in manifest.dumps({"greeting": "héllo"})


REPO = "us-docker.pkg.dev/moz-fx-hackbot-prod/hackbot-prod"


def built(n: int, broker: bool = False) -> dict[str, str]:
    images = {"agent": f"{REPO}/a-agent@sha256:{n:064x}"}
    if broker:
        images["broker"] = f"{REPO}/a-broker@sha256:{n:064x}"
    return images


def test_images_are_pinned_when_the_shape_changes() -> None:
    first, _ = build_manifest({"a": config()}, images={"a": built(1)})
    assert first["agents"]["a"]["images"] == built(1)

    entry, change = _regenerate_with(first, config(cpu="8"), images={"a": built(2)})
    assert change is Change.EXPAND
    assert entry["images"] == built(2)


def test_images_for_an_unchanged_shape_are_ignored() -> None:
    first, _ = build_manifest({"a": config()}, images={"a": built(1)})
    entry, change = _regenerate_with(first, config(), images={"a": built(2)})
    assert change is Change.UNCHANGED
    assert entry["images"] == built(1)


def test_a_changed_shape_without_images_has_none() -> None:
    first, _ = build_manifest({"a": config()}, images={"a": built(1)})
    entry, _ = _regenerate_with(first, config(cpu="8"))
    assert "images" not in entry


def test_pending_images_are_kept_for_the_same_shape_only() -> None:
    main, _ = build_manifest({"a": config(), "b": config()})
    # A run for b, after a run for a pinned a's images in the bot PR.
    pending, _ = build_manifest(
        {"a": config(cpu="8"), "b": config()}, main, images={"a": built(1)}
    )
    generated, _ = build_manifest(
        {"a": config(cpu="8"), "b": config(cpu="8")},
        main,
        pending=pending,
        images={"b": built(2)},
    )
    assert generated["agents"]["a"]["images"] == built(1)
    assert generated["agents"]["b"]["images"] == built(2)

    # a's shape moved on again: what the PR pinned was built for the old one.
    generated, _ = build_manifest(
        {"a": config(cpu="10"), "b": config()}, main, pending=pending
    )
    assert "images" not in generated["agents"]["a"]


def test_images_must_match_the_containers() -> None:
    with pytest.raises(manifest.ManifestError, match="broker"):
        build_manifest({"a": config(broker={})}, images={"a": built(1)})
    with pytest.raises(manifest.ManifestError, match="no \\[deploy\\] table"):
        build_manifest({"a": config()}, images={"z": built(1)})


def test_images_must_be_digests() -> None:
    with pytest.raises(Exception, match="digest"):
        build_manifest({"a": config()}, images={"a": {"agent": f"{REPO}/a:latest"}})


def test_images_do_not_change_the_hash() -> None:
    plain, _ = build_manifest({"a": config()})
    pinned, _ = build_manifest({"a": config()}, images={"a": built(1)})
    assert plain["agents"]["a"]["config_hash"] == pinned["agents"]["a"]["config_hash"]


def _regenerate_with(
    previous: dict, cfg: DeployConfig, **kw: object
) -> tuple[dict, Change]:
    generated, changes = build_manifest({"a": cfg}, previous, **kw)  # type: ignore[arg-type]
    return generated["agents"]["a"], changes["a"]
