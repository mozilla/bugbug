"""Generate ``agents.json`` from every agent's ``[deploy]`` table.

``agents.json`` is what the Terraform in mozilla/webservices-infra reads to
create agent infrastructure. Each entry carries two bookkeeping keys besides
the shape itself:

- ``config_hash`` identifies the shape. Cloud Build bakes it into the agent's
  image as a label, and the deploy script only puts an image onto
  infrastructure whose applied hash matches.
- ``compatible`` lists the shapes the current infrastructure can still run.
  A rollback is allowed onto any image whose hash is in it.

An agent without a ``[deploy]`` table isn't deployed yet and is left out.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hackbot_deploy.schema import DeployConfig

SCHEMA_VERSION = 1
HASH_ALGORITHM = "sha256-canonical-json-v1"
GENERATOR = "hackbot-deploy"

# How many shapes back a rollback can reach. Every expand appends one; a
# contract resets the list. Older shapes fall off the front.
MAX_COMPATIBLE = 20

_BOOKKEEPING = ("config_hash", "compatible")


class ManifestError(Exception):
    """An agent's ``[deploy]`` table, or the previous manifest, is invalid."""


class Change(StrEnum):
    """What a regeneration does to one agent."""

    ADDED = "added"
    REMOVED = "removed"
    UNCHANGED = "unchanged"
    # Infrastructure the old image still runs on: rollback stays possible.
    EXPAND = "expand"
    # Infrastructure the old image may not run on: rollback is cut off.
    CONTRACT = "contract"


def config_hash(name: str, entry: dict[str, Any]) -> str:
    """Hash an agent's shape.

    sha256 over the canonical JSON of the entry with the agent's name added and
    the bookkeeping keys removed: sorted keys, no whitespace, UTF-8, first 16
    hex characters. The name is included so one agent's image can never pass
    another agent's gate. webservices-infra documents this same algorithm, and
    the two must never drift.
    """
    body = {k: v for k, v in entry.items() if k not in _BOOKKEEPING}
    body["name"] = name
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def load_config(path: Path) -> DeployConfig | None:
    """Return the ``[deploy]`` table of one ``hackbot.toml``, or None without one."""
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    if "deploy" not in data:
        return None
    try:
        return DeployConfig.model_validate(data["deploy"])
    except ValidationError as exc:
        raise ManifestError(f"{path}: invalid [deploy] table\n{exc}") from exc


def load_configs(agents_dir: Path) -> dict[str, DeployConfig]:
    """Return every deployable agent under ``agents_dir``, keyed by directory name."""
    configs: dict[str, DeployConfig] = {}
    for path in sorted(agents_dir.glob("*/hackbot.toml")):
        config = load_config(path)
        if config is not None:
            configs[path.parent.name] = config
    return configs


def classify(previous: dict[str, Any], current: dict[str, Any]) -> Change:
    """Decide whether moving from one shape to another is an expand or a contract.

    A contract is anything an image built for the old shape might not survive:
    a different runtime, a different container set (a broker added or removed),
    a workspace taken away, or an environment variable it may read taken away.
    Everything else, sizing included, is an expand.
    """
    if previous.get("runtime") != current.get("runtime"):
        return Change.CONTRACT
    if ("broker" in previous) != ("broker" in current):
        return Change.CONTRACT
    if "workspace" in previous and "workspace" not in current:
        return Change.CONTRACT

    def removed_keys(old: dict[str, Any], new: dict[str, Any], key: str) -> bool:
        return bool(set(old.get(key, {})) - set(new.get(key, {})))

    for key in ("env", "secret_env"):
        if removed_keys(previous, current, key):
            return Change.CONTRACT
        if removed_keys(previous.get("broker", {}), current.get("broker", {}), key):
            return Change.CONTRACT
    return Change.EXPAND


def build_manifest(
    configs: dict[str, DeployConfig],
    previous: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Change]]:
    """Build ``agents.json`` and say what changed for each agent.

    ``previous`` is the manifest currently in webservices-infra. It is needed
    to carry each agent's ``compatible`` list forward; without it every agent
    starts fresh, which is only right the first time.
    """
    previous_agents: dict[str, Any] = {}
    if previous is not None:
        if previous.get("schema_version") != SCHEMA_VERSION:
            raise ManifestError(
                f"previous manifest has schema_version {previous.get('schema_version')}; expected {SCHEMA_VERSION}"
            )
        previous_agents = previous.get("agents", {})

    agents: dict[str, Any] = {}
    changes: dict[str, Change] = {}
    for name in sorted(configs):
        entry = configs[name].manifest_entry()
        digest = config_hash(name, entry)
        before = previous_agents.get(name)

        if before is None:
            change, compatible = Change.ADDED, [digest]
        elif before["config_hash"] == digest:
            change, compatible = Change.UNCHANGED, list(before["compatible"])
        else:
            change = classify(before, entry)
            if change is Change.EXPAND:
                compatible = [h for h in before["compatible"] if h != digest] + [digest]
                compatible = compatible[-MAX_COMPATIBLE:]
            else:
                compatible = [digest]

        agents[name] = {**entry, "config_hash": digest, "compatible": compatible}
        changes[name] = change

    for name in previous_agents.keys() - configs.keys():
        changes[name] = Change.REMOVED

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "hash_algorithm": HASH_ALGORITHM,
        "agents": agents,
    }
    return manifest, changes


def dumps(manifest: dict[str, Any]) -> str:
    """Serialise a manifest exactly as webservices-infra's pretty-format-json hook would.

    4-space indent, key order kept, non-ASCII escaped. Anything else and the
    hook rewrites the file on every bot PR.
    """
    return json.dumps(manifest, indent=4) + "\n"
