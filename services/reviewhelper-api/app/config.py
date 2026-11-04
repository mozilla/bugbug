from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Cloud SQL - Production (Cloud Run)
    cloud_sql_instance: str
    db_user: str
    db_pass: str
    db_name: str

    # Authentication
    external_api_key: str
    internal_api_key: str

    # Cloud Tasks
    cloud_tasks_project: str
    cloud_tasks_location: str
    cloud_tasks_queue: str
    worker_url: str

    # Phabricator
    phabricator_url: str
    phabricator_api_key: str

    # Optional until a GitHub App is registered and installed.
    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_webhook_secret: str | None = None
    github_review_delay_seconds: int = Field(default=30, ge=0, le=300)
    # Enrolled repositories, as a JSON list of "owner/repo".
    github_repositories: list[str] = ["padenot/searchfox-cli"]
    github_automatic_reviews: bool = True
    github_allow_author_requests: bool = True

    # Bugzilla
    bugzilla_url: str
    bugzilla_api_key: str

    # Environment
    environment: str = "development"

    # Sentry
    sentry_dsn: str = "https://14980b902e7207de7ffe7f00ea5d8aef@o1069899.ingest.us.sentry.io/4510918097371136"

    # Cloud Run
    port: int = 8080

    # Agent settings
    todo_enabled: bool = True

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
