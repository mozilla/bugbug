"""Configuration for :class:`LandoClient`."""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Lando deployment names, keyed by the host they serve. This pairing is not
# something Lando exposes over its API: `instance_id` is a *client-side* label,
# declared in mozilla-central's `.lando.ini` (one `instance_id`/`api_domain`
# pair per section) and mapped back to a host by Treeherder itself.
INSTANCE_IDS_BY_HOST = {
    "lando.moz.tools": "lando-prod-2025",
    "lando-dev.allizom.org": "lando-dev-2025",
    "api.lando.services.mozilla.com": "lando-prod",
    "api.dev.lando.nonprod.cloudops.mozgcp.net": "lando-dev",
}


class LandoSettings(BaseModel):
    """Where Lando lives and how to authenticate against it.

    ``access_token`` is an OIDC bearer token for the account that owns the
    pushes: Lando's Try endpoint authenticates the *user* (it has no API-key
    mode), and the try repository's permissions are checked against that user.
    """

    access_token: str = Field(min_length=1)
    url: str = "https://lando.moz.tools"
    instance_id: str | None = None
    timeout_seconds: int = 60

    @model_validator(mode="after")
    def _resolve_instance_id(self) -> LandoSettings:
        host = urlsplit(self.url).hostname
        resolved = INSTANCE_IDS_BY_HOST.get(host)
        if not resolved and not self.instance_id:
            known_hosts = ", ".join(sorted(INSTANCE_IDS_BY_HOST))
            raise ValueError(
                f"Unknown Lando host {host!r}: cannot tell which deployment "
                "Treeherder should link to. Set LANDO_INSTANCE_ID explicitly "
                f"(known hosts: {known_hosts})."
            )
        if resolved and self.instance_id and self.instance_id != resolved:
            raise ValueError(
                f"LANDO_INSTANCE_ID {self.instance_id!r} does not match the "
                f"known deployment for {host!r} ({resolved!r})."
            )
        if resolved and not self.instance_id:
            self.instance_id = resolved

        return self

    @classmethod
    def from_env(cls) -> LandoSettings:
        return _LandoEnvSettings()


class _LandoEnvSettings(LandoSettings, BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LANDO_", extra="ignore")
