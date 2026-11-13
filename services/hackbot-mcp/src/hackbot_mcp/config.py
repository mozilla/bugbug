from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Public URL of this server, as MCP clients reach it (no trailing /mcp).
    # The OAuth metadata, the token audience and Google's redirect URI
    # (<base_url>/auth/callback) all derive from it, so it must be stable.
    base_url: str

    # Google OAuth client (Web application) whose redirect URI is
    # <base_url>/auth/callback.
    google_client_id: str
    google_client_secret: SecretStr

    # Signs the tokens this server issues to MCP clients.
    jwt_signing_key: SecretStr
    # Encrypts the OAuth state at rest, which includes users' Google tokens.
    storage_encryption_key: SecretStr

    # OAuth state storage, shared by every instance. A Cloud SQL instance in
    # production; a plain Postgres URL locally; in memory only in development.
    cloud_sql_instance: str = ""
    cloud_sql_ip_type: Literal["PUBLIC", "PRIVATE", "PSC"] = "PUBLIC"
    db_user: str = ""
    db_pass: SecretStr = SecretStr("")
    db_name: str = "hackbot_mcp"
    database_url: SecretStr = SecretStr("")

    # hackbot-api. Without an API key, the client authenticates with this
    # service's own Google identity (the metadata server's ID token, audience
    # hackbot_api_url), which hackbot-api must list in ALLOWED_SERVICE_ACCOUNTS.
    hackbot_api_url: str = "https://hackbot-api.moz.tools"
    hackbot_api_key: SecretStr = SecretStr("")
    hackbot_ui_url: str = "https://hackbot.moz.tools"

    port: int = 8080
    environment: str = "development"
    sentry_dsn: str | None = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "hide_input_in_errors": True,
    }

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        for name in ("jwt_signing_key", "storage_encryption_key"):
            if len(getattr(self, name).get_secret_value()) < 32:
                raise ValueError(f"{name} must be at least 32 characters")
        if (
            not self.cloud_sql_instance
            and not self.database_url.get_secret_value()
            and self.environment != "development"
        ):
            # Instances would each keep their own registrations and tokens,
            # so clients would fail at random depending on the instance hit.
            raise ValueError(
                "CLOUD_SQL_INSTANCE or DATABASE_URL is required outside development"
            )
        self.base_url = self.base_url.rstrip("/")
        self.hackbot_api_url = self.hackbot_api_url.rstrip("/")
        self.hackbot_ui_url = self.hackbot_ui_url.rstrip("/")
        return self
