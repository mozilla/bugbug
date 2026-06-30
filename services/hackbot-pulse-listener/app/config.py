from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Pulse (https://pulseguardian.mozilla.org)
    pulse_user: str = ""
    pulse_password: str = ""
    taskcluster_root_url: str = "https://firefox-ci-tc.services.mozilla.com"

    # hackbot-api
    hackbot_api_url: str = ""
    hackbot_api_key: str = ""
    agent_name: str = "build-repair"

    # Failure filtering and agent inputs.
    # ``watched_repos`` is a comma-separated list of Taskcluster ``project`` tags.
    watched_repos: str = "try,autoland"
    run_try_push: bool = False
    model: str | None = None
    max_turns: int | None = None

    # Dedupe (in-memory, by git revision)
    dedupe_ttl_seconds: int = 6 * 60 * 60
    dedupe_max_size: int = 4096

    dry_run: bool = False
    environment: str = "development"
    sentry_dsn: str | None = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def watched_repos_set(self) -> set[str]:
        return {r.strip() for r in self.watched_repos.split(",") if r.strip()}


settings = Settings()
