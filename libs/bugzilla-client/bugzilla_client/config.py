"""Configuration for :class:`BugzillaClient`.

``BugzillaSettings`` is a plain, validated config model with no env I/O, so it
can be embedded in a larger settings object without triggering a second
environment parse. Use :meth:`BugzillaSettings.from_env` for standalone,
env-driven config (``BUGZILLA_URL``, ``BUGZILLA_API_KEY``, ...).
"""

from importlib.metadata import version
from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BugzillaSettings(BaseModel):
    # Bugzilla generates API keys as 40 random letters and digits.
    api_key: Annotated[str, Field(min_length=40, max_length=40)]
    # The site root, without ``/rest``; the client adds it for API calls.
    url: str = "https://bugzilla.mozilla.org"
    timeout_seconds: float = 30
    edge_key: str | None = None
    user_agent: str = f"Bugbug-BugzillaClient/{version('bugzilla-client')}"

    @classmethod
    def from_env(cls) -> BugzillaSettings:
        return _BugzillaEnvSettings()


class _BugzillaEnvSettings(BugzillaSettings, BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BUGZILLA_", extra="ignore")
