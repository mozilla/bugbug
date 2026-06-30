import logging
import signal

from app import consumer
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)

    if not (settings.pulse_user and settings.pulse_password):
        logger.warning("PULSE_USER/PULSE_PASSWORD not set; listener will not start")
        return

    consumer_obj = consumer.build_consumer()

    def shutdown(signum, _frame):
        logger.info("Received signal %s; shutting down", signum)
        consumer_obj.should_stop = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info(
        "Listening for build failures on %s; watched repos: %s",
        ", ".join(consumer.EXCHANGES),
        sorted(settings.watched_repos_set),
    )
    consumer_obj.run()


if __name__ == "__main__":
    main()
