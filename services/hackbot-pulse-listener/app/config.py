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
    # Agent that analyzes test failures (separate Cloud Run Job from build-repair).
    test_repair_agent_name: str = "test-repair"

    # Source links shown in notifications.
    firefox_git_url: str = "https://github.com/mozilla-firefox/firefox"
    firefox_hg_url: str = "https://hg.mozilla.org/mozilla-unified"
    bugzilla_url: str = "https://bugzilla.mozilla.org"
    treeherder_url: str = "https://treeherder.mozilla.org"

    # Failure filtering and agent inputs.
    # ``watched_repos`` is a comma-separated list of Taskcluster ``project`` tags.
    watched_repos: str = "autoland"
    run_try_push: bool = False
    model: str | None = None
    max_turns: int | None = None
    # Skip a failing test whose historical failure rate is at or above this
    # (clearly intermittent) before spending a test-repair run. The rate is per
    # *run*, over every platform in the timings window (three weeks, thousands of
    # runs per test), where even the flakiest tests sit near a few percent -- so
    # this is an order of magnitude lower than a per-push failure rate would be.
    flakiness_threshold: float = 0.05

    # Dedupe (in-memory, by hg revision)
    dedupe_ttl_seconds: int = 6 * 60 * 60
    dedupe_max_size: int = 4096

    # Polling the API for run completion
    poll_interval_seconds: int = 60
    run_max_age_minutes: int = 12 * 60
    # Shared worker pool for message processing and run polling. A
    # regression check may block for a few minutes waiting for a parent
    # build to settle, so the pool is sized well above the number of
    # builds/runs in flight at once. Threads are cheap and mostly idle
    # while waiting.
    max_workers: int = 256

    # Email notifications (SendGrid)
    sendgrid_api_key: str | None = None
    notification_sender: str | None = None
    # Team address CC'd on every notification alongside the revision author.
    notification_team_email: str | None = None
    # Distribution address; primary recipient of test-repair verdicts.
    test_repair_notification_email: str | None = None
    # Send all notifications to this address instead of the developer (local testing).
    notification_override_email: str | None = None
    # Only notify when the run produced a patch (skip transient / not-to-blame runs).
    notify_only_with_patch: bool = True

    dry_run: bool = False
    log_level: str = "INFO"
    # mozci's own (loguru) logging. Its per-task "missing results" warnings are
    # normal operation, not something to act on; lower this to debug mozci.
    mozci_log_level: str = "ERROR"
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
