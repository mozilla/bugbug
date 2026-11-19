"""Configuration for :class:`BugzillaClient`.

``BugzillaSettings`` is a plain, validated config model with no env I/O, so it
can be embedded in a larger settings object without triggering a second
environment parse. Use :meth:`BugzillaSettings.from_env` for standalone,
env-driven config (``BUGZILLA_API_URL``, ``BUGZILLA_API_KEY``, ...). It also
accepts the site root as ``BUGZILLA_URL`` when ``BUGZILLA_API_URL`` is unset.
"""

from importlib.metadata import version
from typing import Annotated, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BugzillaSettings(BaseModel):
    # Bugzilla generates API keys as 40 random letters and digits.
    api_key: Annotated[str, Field(min_length=40, max_length=40)]
    # The REST API base, used as is for API calls.
    api_url: str = "https://bugzilla.mozilla.org/rest"
    timeout_seconds: float = 30
    edge_key: str | None = None
    user_agent: str = f"Bugbug-BugzillaClient/{version('bugzilla-client')}"

    @classmethod
    def from_env(cls) -> BugzillaSettings:
        return _BugzillaEnvSettings()


class _BugzillaEnvSettings(BugzillaSettings, BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUGZILLA_", extra="ignore")
    # The site root, as older configs set it; used when BUGZILLA_API_URL is unset.
    url: str | None = None

    @model_validator(mode="after")
    def _api_url_from_site_root(self) -> Self:
        if "api_url" not in self.model_fields_set and self.url:
            self.api_url = self.url.rstrip("/") + "/rest"
        return self
