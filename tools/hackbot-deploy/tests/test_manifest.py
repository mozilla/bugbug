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

REPO_ROOT = Path(__file__).parents[3]
# The hand-written agents.json that webservices-infra started from.
FIXTURE = Path(__file__).parent / "fixtures" / "agents.json"

JOB = {"runtime": "cloud_run_job", "cpu": "6", "memory": "24Gi"}


def config(**fields: object) -> DeployConfig:
    return DeployConfig.model_validate({**JOB, **fields})


def test_reproduces_the_infra_manifest_byte_for_byte() -> None:
    previous = json.loads(FIXTURE.read_text())
    generated, changes = build_manifest(
        manifest.load_configs(REPO_ROOT / "agents"), previous
    )

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
        "a", config(env={}).manifest_entry()
    )


def test_the_hash_ignores_bookkeeping_and_key_order() -> None:
    entry = config(env={"B": "2", "A": "1"}).manifest_entry()
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
        ({}, {"env": {"NEW": "1"}}, Change.EXPAND),
        ({}, {"workspace": {"size": "40Gi"}}, Change.EXPAND),
        ({"env": {"OLD": "1"}}, {}, Change.CONTRACT),
        ({"secret_env": {"KEY": "some-secret"}}, {}, Change.CONTRACT),
        ({"workspace": {"size": "40Gi"}}, {}, Change.CONTRACT),
        # A broker either way changes the container set.
        ({}, {"broker": {}}, Change.CONTRACT),
        ({"broker": {}}, {}, Change.CONTRACT),
        ({"broker": {"env": {"A": "1"}}}, {"broker": {}}, Change.CONTRACT),
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
    generated, _ = build_manifest({"a": config(env={"GREETING": "héllo"})})
    assert "h\\u00e9llo" in manifest.dumps(generated)
