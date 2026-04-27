from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Bugzilla
    bz_base_url: str = ""
    bz_api_key: str = ""

    # Firefox source repo (for bug_fix tool)
    source_repo: str = "/workspace/firefox"

    # Agent
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None

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
