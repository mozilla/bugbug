from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Pulse (https://pulseguardian.mozilla.org)
    pulse_user: str = ""
    pulse_password: str = ""
    taskcluster_root_url: str = "https://firefox-ci-tc.services.mozilla.com"

    # hackbot-api
    hackbot_api_url: str = ""
    hackbot_api_key: str = ""
    hackbot_ui_url: str = ""
    agent_name: str = "build-repair"

    # Source links shown in notifications.
    firefox_git_url: str = "https://github.com/mozilla-firefox/firefox"
    firefox_hg_url: str = "https://hg.mozilla.org/mozilla-unified"
    bugzilla_url: str = "https://bugzilla.mozilla.org"

    # Failure filtering and agent inputs.
    # ``watched_repos`` is a comma-separated list of Taskcluster ``project`` tags.
    watched_repos: str = "autoland"
    run_try_push: bool = False
    model: str | None = None
    max_turns: int | None = None

    # Dedupe (in-memory, by hg revision)
    dedupe_ttl_seconds: int = 6 * 60 * 60
    dedupe_max_size: int = 4096

    # Polling the API for run completion
    poll_interval_seconds: int = 60
    run_max_age_minutes: int = 12 * 60
    poll_max_workers: int = 8

    # Email notifications (SendGrid)
    sendgrid_api_key: str | None = None
    notification_sender: str | None = None
    # Team address CC'd on every notification alongside the revision author.
    notification_team_email: str | None = None
    # Send all notifications to this address instead of the developer (local testing).
    notification_override_email: str | None = None

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
