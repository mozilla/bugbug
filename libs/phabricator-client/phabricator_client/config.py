"""Configuration for :class:`PhabricatorClient`.

``PhabricatorSettings`` is a plain, validated config model with no env I/O, so
it can be embedded in a larger settings object without triggering a second
environment parse. Use :meth:`PhabricatorSettings.from_env` for standalone,
env-driven config (e.g. the agents' Cloud Run Jobs).
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PhabricatorSettings(BaseModel):
    api_key: str = Field(min_length=32, max_length=32)
    url: str = "https://phabricator.services.mozilla.com"
    timeout_seconds: int = 60

    @classmethod
    def from_env(cls) -> PhabricatorSettings:
        return _PhabricatorEnvSettings()


class _PhabricatorEnvSettings(PhabricatorSettings, BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PHABRICATOR_", extra="ignore")
