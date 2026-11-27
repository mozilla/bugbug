"""Configuration, by increasing precedence: defaults, a TOML file, environment
variables (``CRASHINFO_`` prefix, ``__`` between levels, e.g.
``CRASHINFO_INVESTIGATION__MAX_TOOL_CALLS=20``), then explicit overrides.

The TOML file is ``$CRASHINFO_CONFIG`` or ``./crash_info.toml`` when present.
"""

import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from .models import RunMode

DEFAULT_CONFIG_NAME = "crash_info.toml"


class OllamaSettings(BaseModel):
    # Falls back to $OLLAMA_HOST, then http://localhost:11434.
    host: str | None = None
    # The rest applies to the native Ollama client (structured calls), not to the
    # agent, which reaches Ollama through its OpenAI-compatible API.
    num_ctx: int = 16_384
    keep_alive: str = "10m"
    # Reasoning traces of thinking models: "auto" turns them off where possible
    # (qwen3, deepseek-r1) and uses "low" for gpt-oss; true/false or
    # "low"/"medium"/"high" force a setting; None leaves Ollama's default.
    think: bool | Literal["auto", "low", "medium", "high"] | None = "auto"


# The model of standalone runs outside Hackbot, with other providers. The Hackbot
# agent does not use it: it runs on Claude, with its MODEL and EFFORT inputs.
class LLMSettings(BaseModel):
    # openrouter | openai | deepseek | ollama | anthropic | another OpenAI-compatible preset
    # (see `crash-info providers`).
    provider: str = "openrouter"
    # None picks the provider's default model.
    model: str | None = None
    # Overrides the provider's endpoint (e.g. a self-hosted OpenAI-compatible server).
    base_url: str | None = None
    # Name of the environment variable holding the API key; keys never go in config files.
    api_key_env: str | None = None
    temperature: float = 0.0
    # None: 4096 for local models and 32768 for remote ones, whose reasoning tokens
    # count against the limit (2048 / 8192 for the native clients' structured calls).
    max_output_tokens: int | None = None
    timeout_s: float = 300.0
    # The context window, in tokens; None: 16k for local models, 200k for remote ones.
    context_tokens: int | None = None
    ollama: OllamaSettings = OllamaSettings()
    anthropic_version: str = "2023-06-01"


class SourceSettings(BaseModel):
    bugzilla_url: str = "https://bugzilla.mozilla.org"
    # Only needed for restricted bugs; public crash bugs need no key.
    bugzilla_api_key_env: str = "BUGZILLA_API_KEY"
    crash_stats_url: str = "https://crash-stats.mozilla.org"
    searchfox_url: str = "https://searchfox.org"
    searchfox_repo: str = "firefox-main"
    hgmo_url: str = "https://hg.mozilla.org"
    hgmo_repo: str = "mozilla-central"
    # A local Firefox git checkout to search instead of Searchfox (Hackbot's [source]).
    local_repo: Path | None = None
    timeout_s: float = 30.0
    cache_dir: Path | None = Path("~/.cache/crash-info/http")
    cache_ttl_s: int = 86_400
    user_agent: str = "crash-info-agent/0.1 (+https://github.com/mozilla/bugbug)"
    # How far before the cutoff crash-stats aggregates look, in as-of mode.
    crash_stats_window_days: int = 14


class InvestigationSettings(BaseModel):
    # Lookups per investigation: 20 in the architecture proposal; the agent used
    # all of them on 8 of 10 bugs.
    max_tool_calls: int = 30
    # Model turns are bounded by min(2 * max_tool_calls + 6, max_llm_calls).
    max_llm_calls: int = 150
    # Pieces of missing information the triage may report.
    max_gaps: int = 12
    # Earlier bugs fixed before the cutoff that the agent recalls before triage.
    prior_bugs_recalled: int = 3
    # Complete the crash data a report lacks: fetch a stack before triage when the
    # report holds none, and fall back on other crashes of the signature (exact,
    # then "contains") when a crash report is gone or has no usable stack.
    crash_data: bool = True
    # Before finishing, review each answer once against the evidence it cites.
    # Off by default (2026-11-23): about 45% more tokens per analysis for no
    # measured gain (5 bugs), and the best measured configuration ran without it.
    answer_review: bool = False


_config_path: ContextVar[Path | None] = ContextVar("crash_info_config", default=None)


class Settings(BaseSettings):
    llm: LLMSettings = LLMSettings()
    sources: SourceSettings = SourceSettings()
    investigation: InvestigationSettings = InvestigationSettings()
    mode: RunMode = RunMode()
    output_dir: Path = Path("runs")
    # Alternative taxonomy file (same format as the bundled taxonomy.toml).
    taxonomy_path: Path | None = None

    model_config = SettingsConfigDict(
        env_prefix="CRASHINFO_", env_nested_delimiter="__", extra="ignore"
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
        path = _config_path.get()
        if path is not None:
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=path))
        return tuple(sources)


def resolve_config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(f"config file {explicit} does not exist")
        return explicit
    from_env = os.environ.get("CRASHINFO_CONFIG")
    if from_env:
        return resolve_config_path(Path(from_env))
    local = Path.cwd() / DEFAULT_CONFIG_NAME
    return local if local.exists() else None


def load_settings(
    config_path: Path | None = None, overrides: dict[str, Any] | None = None
) -> Settings:
    """Build settings; ``overrides`` is a nested dict that wins over everything."""
    token = _config_path.set(resolve_config_path(config_path))
    try:
        return Settings(**_drop_none(overrides or {}))
    finally:
        _config_path.reset(token)


def _drop_none(data: dict[str, Any]) -> dict[str, Any]:
    """Unset CLI flags arrive as None and must not mask lower-precedence values."""
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            value = _drop_none(value)
            if value:
                cleaned[key] = value
        elif value is not None:
            cleaned[key] = value
    return cleaned
