"""Declarative agent configuration loaded from ``hackbot.toml``.

Captures the capability declarations that are intrinsic to an agent (which
source repo it operates on, whether it needs a Firefox build) so the runtime
can prepare them on the agent's behalf. Per-run inputs and secrets are NOT here
— they arrive via environment variables.
"""

import tempfile
import tomllib
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings


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


class WandbAuthSettings(BaseSettings):
    wandb_api_key: SecretStr = Field(validation_alias="WANDB_API_KEY")

    @property
    def api_key(self) -> str:
        return self.wandb_api_key.get_secret_value()


class AnthropicAuthSettings(BaseSettings):
    anthropic_api_key: SecretStr = Field(validation_alias="ANTHROPIC_API_KEY")

    @property
    def api_key(self) -> str:
        return self.anthropic_api_key.get_secret_value()


class OpenaiAuthSettings(BaseSettings):
    openai_api_key: SecretStr = Field(validation_alias="OPENAI_API_KEY")

    @property
    def api_key(self) -> str:
        return self.openai_api_key.get_secret_value()


class WandbWifSettings(BaseSettings):
    # Presence of the audience is what flips the runtime from API-key mode to WIF
    # mode — the deploy sets it only on federation-enabled workloads. Its value is
    # the audience the Google identity token is minted for, which must match the
    # W&B org's federation trust configuration.
    AUDIENCE_ENV: ClassVar[str] = "WANDB_FEDERATION_AUDIENCE"
    TOKEN_FILE_ENV: ClassVar[str] = "WANDB_IDENTITY_TOKEN_FILE"
    REFRESH_INTERVAL_ENV: ClassVar[str] = "WANDB_IDENTITY_TOKEN_REFRESH_SECONDS"

    # Google identity tokens expire after ~1h; refresh well inside that window so
    # the file always carries plenty of remaining lifetime for the SDK to exchange.
    DEFAULT_REFRESH_INTERVAL: ClassVar[float] = 1800

    def _default_token_path() -> Path:
        """A private, process-owned path for the identity token file.

        ``mkdtemp`` gives a 0700 directory so the bearer token isn't world-readable.
        """
        return Path(tempfile.mkdtemp(prefix="wandb-wif-")) / "identity-token"

    audience: str | None = Field(
        default=None,
        validation_alias="WANDB_FEDERATION_AUDIENCE",
    )

    token_file: Path = Field(
        default_factory=_default_token_path(),
        validation_alias="WANDB_IDENTITY_TOKEN_FILE",
    )

    refresh_interval: str | None = Field(
        default=None,
        validation_alias="WANDB_IDENTITY_TOKEN_REFRESH_SECONDS",
    )


class OpenaiWifSettings(BaseSettings):
    # Default audience the metadata token is minted for; must match the federation
    # rule's audience on the OpenAI side. Overridable via ``AUDIENCE_ENV``.
    OPENAI_AUDIENCE: ClassVar[str] = "https://api.openai.com/v1"

    # Presence of the federation rule id is what flips the runtime from API-key mode
    # to WIF mode — the deploy sets it only on federation-enabled workloads.
    RULE_ID_ENV: ClassVar[str] = "OPENAI_FEDERATION_RULE_ID"
    AUDIENCE_ENV: ClassVar[str] = "OPENAI_WIF_AUDIENCE"
    TOKEN_FILE_ENV: ClassVar[str] = "OPENAI_IDENTITY_TOKEN_FILE"
    REFRESH_INTERVAL_ENV: ClassVar[str] = "OPENAI_IDENTITY_TOKEN_REFRESH_SECONDS"

    # Google identity tokens expire after ~1h; refresh well inside that window so
    # the file always carries plenty of remaining lifetime for Codex to exchange.
    DEFAULT_REFRESH_INTERVAL: ClassVar[float] = 1800

    def _default_token_path(self) -> Path:
        """A private, process-owned path for the identity token file.

        ``mkdtemp`` gives a 0700 directory so the bearer token isn't world-readable.
        The path is absolute, as Codex requires.
        """
        return Path(tempfile.mkdtemp(prefix="openai-wif-")) / "identity-token"

    rule_id: str | None = Field(
        default=None,
        validation_alias="OPENAI_FEDERATION_RULE_ID",
    )

    token_file: Path = Field(
        default_factory=_default_token_path(),
        validation_alias="OPENAI_IDENTITY_TOKEN_FILE",
    )

    audience: str | None = Field(
        default=OPENAI_AUDIENCE,
        validation_alias="OPENAI_WIF_AUDIENCE",
    )
    refresh_interval: str | None = Field(
        default=None,
        validation_alias="OPENAI_IDENTITY_TOKEN_REFRESH_SECONDS",
    )


class AnthropicWifSettings(BaseSettings):
    # Audience the metadata token is minted for; must match the federation rule's
    # ``audience`` matcher on the Anthropic side.
    ANTHROPIC_AUDIENCE: ClassVar[str] = "https://api.anthropic.com"

    # Presence of the federation rule id is what flips the runtime from API-key mode
    # to WIF mode — the deploy sets it only on federation-enabled workloads.
    RULE_ID_ENV: ClassVar[str] = "ANTHROPIC_FEDERATION_RULE_ID"
    TOKEN_FILE_ENV: ClassVar[str] = "ANTHROPIC_IDENTITY_TOKEN_FILE"
    REFRESH_INTERVAL_ENV: ClassVar[str] = "ANTHROPIC_IDENTITY_TOKEN_REFRESH_SECONDS"

    # Google identity tokens expire after ~1h; refresh well inside that window so
    # the file always carries plenty of remaining lifetime for the SDK to exchange.
    DEFAULT_REFRESH_INTERVAL: ClassVar[float] = 1800

    def _default_token_path() -> Path:
        """A private, process-owned path for the identity token file.

        ``mkdtemp`` gives a 0700 directory so the bearer token isn't world-readable.
        """
        return Path(tempfile.mkdtemp(prefix="anthropic-wif-")) / "identity-token"

    rule_id: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_FEDERATION_RULE_ID",
    )

    token_file: Path = Field(
        default_factory=_default_token_path,
        validation_alias="ANTHROPIC_IDENTITY_TOKEN_FILE",
    )

    refresh_interval: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_IDENTITY_TOKEN_REFRESH_SECONDS",
    )
