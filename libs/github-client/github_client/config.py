"""Configuration for :class:`GitHubApp`.

``GitHubAppSettings`` is a plain, validated config model with no env I/O, so it
can be embedded in a larger settings object without triggering a second
environment parse. Use :meth:`GitHubAppSettings.from_env` for standalone,
env-driven config.
"""

from typing import Annotated

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GitHubAppSettings(BaseModel):
    # Never echo the private key back in a validation error.
    model_config = ConfigDict(hide_input_in_errors=True)

    app_id: Annotated[int, Field(gt=0)]
    private_key: SecretStr
    # Default installation for callers that don't pass one, e.g. a deployment
    # that only ever talks to one org.
    installation_id: Annotated[int | None, Field(gt=0)] = None

    @field_validator("private_key")
    @classmethod
    def _check_private_key(cls, value: SecretStr) -> SecretStr:
        # Env vars and secret stores often flatten the PEM's newlines to "\n".
        pem = value.get_secret_value().replace("\\n", "\n").strip()
        try:
            key = load_pem_private_key(pem.encode(), password=None)
        except ValueError, TypeError:
            key = None
        if not isinstance(key, RSAPrivateKey):
            raise ValueError(
                "must be the GitHub App's PEM-encoded RSA private key "
                "(-----BEGIN RSA PRIVATE KEY----- ...)"
            )
        return SecretStr(pem)

    @classmethod
    def from_env(cls) -> GitHubAppSettings:
        return _GitHubAppEnvSettings()


class _GitHubAppEnvSettings(GitHubAppSettings, BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GITHUB_", extra="ignore", hide_input_in_errors=True
    )
