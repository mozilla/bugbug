from pydantic_settings import BaseSettings


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
    sentry_dsn: str | None = (
        "https://0627922a12291ec4313be63aea828247@o1069899.ingest.us.sentry.io/4511785557295104"
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
