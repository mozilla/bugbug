"""The ``[deploy]`` table of an agent's ``hackbot.toml``.

It describes the infrastructure an agent runs on: its runtime, its sizing, any
extra environment, and an optional broker and workspace disk. Everything here
is part of the agent's *shape*. Terraform applies it, so changing any field
changes the agent's ``config_hash`` and goes through an infra PR. The image is
deliberately absent: it ships on its own, without one.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SECRET_NAME = re.compile(r"^[a-z][a-z0-9-]*$")


def _number_to_str(value: Any) -> Any:
    # `cpu = 6` reads naturally in TOML; Cloud Run wants the string "6".
    return (
        str(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else value
    )


Cpu = Annotated[
    str, BeforeValidator(_number_to_str), Field(pattern=r"^\d+(\.\d+)?$|^\d+m$")
]
Memory = Annotated[str, Field(pattern=r"^\d+(Mi|Gi)$")]


def _check_env(env: dict[str, str]) -> dict[str, str]:
    bad = [name for name in env if not _ENV_NAME.match(name)]
    if bad:
        raise ValueError(f"not valid environment variable names: {', '.join(bad)}")
    return env


def _check_secret_env(secret_env: dict[str, str]) -> dict[str, str]:
    _check_env(secret_env)
    bad = [secret for secret in secret_env.values() if not _SECRET_NAME.match(secret)]
    if bad:
        raise ValueError(f"not valid Secret Manager secret names: {', '.join(bad)}")
    return secret_env


class _Strict(BaseModel):
    # An unknown key is a typo, not an extension: a misspelt `memory_gb` must fail
    # loudly rather than quietly leave the agent on a default.
    model_config = ConfigDict(extra="forbid")


class Workspace(_Strict):
    """A disk-backed scratch volume mounted at ``/workspace``."""

    size: Memory


class Broker(_Strict):
    """The sidecar that holds the agent's credentials.

    The agent reaches it over localhost and never sees the keys. It is
    configured entirely at deploy time and receives no per-run overrides.
    """

    cpu: Cpu = "1"
    memory: Memory = "512Mi"
    # Environment variable -> Secret Manager secret name. Names, never values.
    secret_env: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)

    _validate_env = field_validator("env")(_check_env)
    _validate_secret_env = field_validator("secret_env")(_check_secret_env)


class DeployConfig(_Strict):
    """An agent's ``[deploy]`` table."""

    runtime: Literal["cloud_run_job"]
    cpu: Cpu
    memory: Memory
    timeout_seconds: Annotated[int, Field(gt=0)] = 7200
    # Static environment on top of what every agent gets. Per-run inputs are
    # not here; the API sends those with each execution.
    env: dict[str, str] = Field(default_factory=dict)
    secret_env: dict[str, str] = Field(default_factory=dict)
    workspace: Workspace | None = None
    broker: Broker | None = None

    _validate_env = field_validator("env")(_check_env)
    _validate_secret_env = field_validator("secret_env")(_check_secret_env)

    def manifest_entry(self) -> dict[str, Any]:
        """Return this agent's ``agents.json`` entry, minus the bookkeeping keys.

        Field order is fixed and map keys are sorted, so reordering a TOML file
        never churns the generated one. Empty maps are omitted, so an empty
        table and a missing one describe the same shape and hash the same.
        """
        entry: dict[str, Any] = {
            "runtime": self.runtime,
            "cpu": self.cpu,
            "memory": self.memory,
            "timeout_seconds": self.timeout_seconds,
        }
        if self.env:
            entry["env"] = dict(sorted(self.env.items()))
        if self.secret_env:
            entry["secret_env"] = dict(sorted(self.secret_env.items()))
        if self.workspace is not None:
            entry["workspace"] = {"size": self.workspace.size}
        if self.broker is not None:
            broker: dict[str, Any] = {
                "cpu": self.broker.cpu,
                "memory": self.broker.memory,
            }
            if self.broker.secret_env:
                broker["secret_env"] = dict(sorted(self.broker.secret_env.items()))
            if self.broker.env:
                broker["env"] = dict(sorted(self.broker.env.items()))
            entry["broker"] = broker
        return entry
