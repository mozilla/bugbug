"""Configuration for :class:`TestRailClient`."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TestRailSettings(BaseModel):
    username: Annotated[str, Field(min_length=1)]
    api_key: Annotated[str, Field(min_length=1)]
    project_id: int
    url: str = "https://mozilla.testrail.io"
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> TestRailSettings:
        return _TestRailEnvSettings()


class _TestRailEnvSettings(TestRailSettings, BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TESTRAIL_", extra="ignore")
