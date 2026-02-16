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

    # Bugzilla
    bugzilla_url: str
    bugzilla_api_key: str

    # Cloud Run
    port: int = 8080

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
