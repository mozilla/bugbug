from phabricator_client import PhabricatorSettings
from pydantic import BaseModel
from pydantic_settings import BaseSettings


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
    # Best-effort in-memory dedupe of retried deliveries, by transaction.
    dedupe_ttl_seconds: int = 6 * 60 * 60


class Settings(BaseSettings):
    # GCP
    gcp_project: str = ""
    gcp_region: str = "us-central1"
    results_bucket: str = ""

    # Cloud SQL Postgres
    cloud_sql_instance: str = ""
    db_user: str = ""
    db_pass: str = ""
    db_name: str = "hackbot"

    # Cloud Run Jobs
    job_execution_timeout_seconds: int = 8 * 60 * 60
    signed_policy_max_bytes: int = 5 * 1024 * 1024 * 1024
    signed_policy_grace_seconds: int = 60 * 60

    # API auth
    external_api_key: str = ""

    # Phabricator Conduit connection config, embedded as a nested model and
    # populated in this single settings parse from PHABRICATOR_URL /
    # PHABRICATOR_API_KEY / PHABRICATOR_TIMEOUT_SECONDS (see env_nested_delimiter
    # below). Injected directly as PhabricatorClient(settings.phabricator).
    # Required, so a missing/invalid api_key fails at startup.
    phabricator: PhabricatorSettings

    # Inbound webhook receiver config, embedded as a nested model and populated
    # from WEBHOOK_<FIELD> env vars in this single parse (WEBHOOK_SECRET,
    # WEBHOOK_BOT_PHID, WEBHOOK_MENTION_TOKEN, WEBHOOK_DEDUPE_TTL_SECONDS).
    # Required via its `secret` field, so WEBHOOK_SECRET must be set at startup.
    webhook: WebhookSettings

    # The webhook receiver triggers runs over the public API (rather than calling
    # the DB/jobs internals directly), so splitting it into its own service later
    # is just a matter of repointing this at the remote API. While co-located,
    # this is a loopback call to the same service, authed with external_api_key.
    hackbot_api_url: str = "http://localhost:8080"

    # Internal event routes (Eventarc / Pub/Sub push targets).
    # Event topics follow a per-domain convention, `<domain>-events` (the GCP
    # project is hackbot-only, so no `hackbot-` prefix); this is the agent-run
    # domain. New domains add their own topic + setting rather than overloading
    # this one (see app/pubsub.py).
    run_events_topic: str = "agent-run-events"
    push_auth_audience: str = ""
    push_auth_service_account: str = ""

    # Server
    port: int = 8080
    environment: str = "development"
    sentry_dsn: str | None = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        # Populate the nested `phabricator` / `webhook` models from
        # PHABRICATOR_<FIELD> / WEBHOOK_<FIELD> env vars in this single parse.
        # max_split=1 splits only on the first underscore, so PHABRICATOR_API_KEY
        # -> phabricator.api_key (not phabricator.api.key) and flat fields still
        # bind to their own exact env var names.
        "env_nested_delimiter": "_",
        "env_nested_max_split": 1,
    }


settings = Settings()
