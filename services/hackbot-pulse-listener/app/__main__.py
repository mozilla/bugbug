import logging
import os
import signal
from concurrent.futures import ThreadPoolExecutor

from app import consumer
from app.config import settings

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# httpx logs every request at INFO, which drowns out our own lines.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


def main() -> None:
    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)

    if not (settings.pulse_user and settings.pulse_password):
        logger.warning("PULSE_USER/PULSE_PASSWORD not set; listener will not start")
        return

    executor = ThreadPoolExecutor(max_workers=settings.max_workers)
    consumer_obj = consumer.build_consumer(executor)

    def shutdown(signum, _frame):
        logger.info("Received signal %s; shutting down", signum)
        consumer_obj.should_stop = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info(
        "Listening for build and test failures on %s; watched repos: %s%s",
        ", ".join(consumer.EXCHANGES),
        sorted(settings.watched_repos_set),
        " (DRY RUN: no agent runs will be triggered)" if settings.dry_run else "",
    )
    code = 0
    try:
        consumer_obj.run()
    except Exception:
        logger.exception("Consumer loop failed")
        code = 1
    finally:
        _exit_now(executor, code)


def _exit_now(executor: ThreadPoolExecutor, code: int) -> None:
    """Drop in-flight work and exit immediately.

    Worker threads block for a long time by design (a regression check waits up
    to an hour for an ancestor push, run polling up to ``run_max_age_minutes``),
    and ThreadPoolExecutor's threads are non-daemon: its atexit hook joins them,
    so a normal exit would hang for hours and Ctrl+C would appear to do nothing.
    That hook also breaks any thread still submitting to a pool mid-shutdown.
    In-flight work is disposable (pending runs are only tracked in memory), so
    abandon it rather than wait.
    """
    executor.shutdown(wait=False, cancel_futures=True)
    logging.shutdown()
    os._exit(code)


if __name__ == "__main__":
    main()
