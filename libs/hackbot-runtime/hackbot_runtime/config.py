"""Declarative agent configuration loaded from ``hackbot.toml``.

Captures the capability declarations that are intrinsic to an agent (which
source repo it operates on, whether it needs a Firefox build) so the runtime
can prepare them on the agent's behalf. Per-run inputs and secrets are NOT here
— they arrive via environment variables.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel


class SourceConfig(BaseModel):
    """The source repository an agent operates on (see ``ensure_source_repo``)."""

    repo_url: str
    # Where the checkout lands. The env var SOURCE_REPO overrides this at runtime
    # (the orchestrator points it at the task-local workspace).
    checkout_path: Path = Path("/workspace/source")
    # Optional commit/branch/tag to check out instead of remote HEAD. The env var
    # SOURCE_REF overrides this at runtime (per-run inputs like a failure commit).
    ref: str | None = None


class FirefoxConfig(BaseModel):
    """Firefox build the agent needs (paths derived from the source checkout)."""

    enabled: bool = True
    # Object directory name under the source root; matches the agent-tools
    # FirefoxContext default.
    objdir: str = "objdir-ff-asan"


class HackbotConfig(BaseModel):
    """Parsed ``hackbot.toml``. Every table is optional.

    An agent that does not operate on a repo omits ``[source]``; one that does
    not need Firefox omits ``[firefox]``. A missing file yields an empty config.
    """

    source: SourceConfig | None = None
    firefox: FirefoxConfig | None = None


def load_config(path: Path) -> HackbotConfig:
    """Load and validate ``hackbot.toml`` at ``path``.

    Strict: the file must exist. The "agent declares no capabilities" fallback is
    handled by discovery (``_resolve_config`` returns an empty
    :class:`HackbotConfig` when no toml is found), which never passes a missing
    path here.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file {path} does not exist")

    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return HackbotConfig.model_validate(data)
