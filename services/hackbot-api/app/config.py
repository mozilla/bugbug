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

    # Server
    port: int = 8080
    environment: str = "development"
    sentry_dsn: str | None = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
