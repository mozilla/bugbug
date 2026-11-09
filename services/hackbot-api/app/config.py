from typing import Annotated, Literal

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from phabricator_client import PhabricatorSettings
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)
from pydantic_settings import BaseSettings

# An HMAC key that must actually be a key. Required alone only rejects a *missing*
# value, and an empty or whitespace one would start a service that rejects every
# delivery it receives -- worse than not starting, because nothing says so.
HmacSecret = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class WebhookSettings(BaseModel):
    """Inbound webhook receiver config (Phabricator "@hackbot" mentions).

    Populated from WEBHOOK_* env vars as part of the single settings parse. Not
    used standalone, so it's a plain model with no env loader of its own.
    """

    # HMAC secret for verifying Phabricator's X-Phabricator-Webhook-Signature.
    # Required (no default): a missing WEBHOOK_SECRET fails at startup rather
    # than silently accepting/rejecting deliveries with an empty secret.
    secret: str
    # The bot's own Phabricator user PHID, so its comments never re-trigger a run.
    bot_phid: str = ""
    # The mention that triggers a bug-fix follow-up run.
    mention_token: str = "@hackbot"
    # Slug of the Phabricator project whose members may trigger a run with a
    # mention, e.g. the project mirroring a Bugzilla group.
    authorized_group: str


class BugzillaWebhookSettings(BaseModel):
    """Inbound Bugzilla ``needinfo?`` webhook configuration."""

    # BMO sends this value verbatim in X-Bugzilla-Webhook-Secret.
    secret: str
    # The Bugzilla account to which the needinfo request must be directed.
    bot_login: str = "hackbot@mozilla.tld"
    # Name of the Bugzilla group whose members may trigger a run with a needinfo.
    authorized_group: str


class SlackSettings(BaseModel):
    """Inbound Slack interactivity config (clicks on the app's own messages).

    Populated from SLACK_* env vars as part of the single settings parse.
    """

    # Slack's app-level signing secret, verifying the HMAC on every interaction
    # delivery.
    signing_secret: HmacSecret
    bot_token: str


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


class Settings(BaseSettings):
    # GCP
    gcp_project: str = ""
    gcp_region: str = "us-central1"
    results_bucket: str = ""

    # Cloud SQL Postgres
    cloud_sql_instance: str = ""
    cloud_sql_ip_type: Literal["PUBLIC", "PRIVATE", "PSC"] = "PUBLIC"
    db_user: str = ""
    db_pass: str = ""
    db_name: str = "hackbot"

    # Cloud Run Jobs
    # Appended to every agent's Job name. Dev and stage share a project, so
    # their Jobs are named hackbot-agent-<agent>-dev / -stage; prod has none.
    agent_job_suffix: str = ""
    job_execution_timeout_seconds: int = 8 * 60 * 60
    signed_policy_max_bytes: int = 5 * 1024 * 1024 * 1024
    signed_policy_grace_seconds: int = 60 * 60

    # Largest run artifact an action handler downloads unless it asks for a
    # different limit. Guards against loading something like a multi-GB patch
    # into memory, which can take the service down.
    action_apply_max_bytes: int = 100 * 1024 * 1024

    # API auth
    external_api_key: str = ""
    allowed_service_accounts: list[str] = []

    phabricator: PhabricatorSettings

    # Optional until the GitHub App credentials are deployed.
    github: GitHubAppSettings | None = None

    webhook: WebhookSettings

    # Bugzilla uses a separate shared-secret header and bot identity. These map
    bugzilla_webhook: BugzillaWebhookSettings

    bugzilla_api_url: str = "https://bugzilla.mozilla.org/rest"
    bugzilla_api_key: str
    bugzilla_edge_key: str | None = None

    slack: SlackSettings

    # The webhook receiver triggers runs over the public API (rather than calling
    # the DB/jobs internals directly), so splitting it into its own service later
    # is just a matter of repointing this at the remote API. While co-located,
    # this is a loopback call to the same service, authed with external_api_key.
    hackbot_api_url: str = "http://localhost:8080"
    hackbot_ui_url: str = "https://hackbot.moz.tools"

    # Internal event routes (Eventarc / Pub/Sub push targets).
    # Event topics follow a per-domain convention, `<domain>-events` (the GCP
    # project is hackbot-only, so no `hackbot-` prefix); this is the agent-run
    # domain. New domains add their own topic + setting rather than overloading
    # this one (see app/pubsub.py).
    run_events_topic: str = "agent-run-events"
    push_auth_audience: str = ""
    push_auth_service_account: str = ""

    # Run-completion email settings (see app/notifications.py).
    sendgrid_api_key: str
    notification_sender: str

    # Server
    port: int = 8080
    environment: str = "development"
    sentry_dsn: str | None = None
    agents_sentry_dsn: str

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "hide_input_in_errors": True,
        # Populate the nested `phabricator` / webhook models from their prefixed
        # env vars in this single parse.
        # max_split=1 splits only on the first underscore, so PHABRICATOR_API_KEY
        # -> phabricator.api_key (not phabricator.api.key) and flat fields still
        # bind to their own exact env var names.
        "env_nested_delimiter": "_",
        "env_nested_max_split": 1,
    }


settings = Settings()
