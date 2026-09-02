from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Pulse (https://pulseguardian.mozilla.org)
    pulse_user: str = ""
    pulse_password: str = ""
    taskcluster_root_url: str = "https://firefox-ci-tc.services.mozilla.com"

    # hackbot-api
    hackbot_api_url: str = ""
    hackbot_api_key: str = ""
    hackbot_ui_url: str = "https://hackbot.moz.tools"
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
    # Pushes older than this are not repaired. A failure can arrive long after its
    # push (a backfill, a long-queued task, a replayed message), and by then the push
    # has been superseded and a sheriff has long since dealt with it. Generous next to
    # the hour or two a push needs to finish building and testing, plus the waits below.
    max_push_age_hours: float = 6
    run_try_push: bool = False
    model: str | None = None
    max_turns: int | None = None
    # Sheriffs classify a failing job shortly after we see the failure, so the
    # gate waits for the job to be ingested before reading that verdict.
    treeherder_ingest_poll_seconds: int = 30
    treeherder_ingest_max_wait_seconds: int = 240
    # How long to wait for a verdict once the job is ingested. Most test failures turn
    # out to be intermittent or expected-fail, so waiting here rejects them before the
    # ancestor walk and before an agent run. Bounded by how late that makes the
    # analysis: classification lands ~1min after the job ends at the median and ~11min
    # at p90, so waiting much past that buys few extra rejections and delays every
    # real regression by the full wait.
    treeherder_classification_wait_seconds: int = 600
    # The wait is skipped for a task whose every failing test looks reliable in the
    # tests.firefox.dev timings data (see the README). 0.05% is the measured knee: a
    # strict 0% covers only two thirds of the genuine regressions that 0.05% does,
    # and looser rates add noise without covering more. The runs floor only rules out
    # tests too new to judge; anything from 30 to 1000 scores the same.
    flakiness_min_runs: int = 100
    flakiness_max_failure_rate: float = 0.0005

    # Dedupe (in-memory, by hg revision)
    dedupe_ttl_seconds: int = 6 * 60 * 60
    dedupe_max_size: int = 4096
    # Dedupe by failing manifest, on top of the per-push one above.
    group_dedupe_ttl_seconds: int = 12 * 60 * 60

    # Cap on test-repair runs started in any rolling 24 hours. Each run clones and
    # builds Firefox in its own container, so a bad day on autoland -- a broken
    # manifest inherited by push after push, or a Treeherder outage that leaves every
    # gate failing open -- could otherwise cost far more than the failures are worth.
    max_test_repairs_per_day: int = 50

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
    # Team address CC'd on every build-repair notification alongside the revision
    # author, and the only recipient of test-repair verdicts.
    notification_team_email: str | None = None
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
