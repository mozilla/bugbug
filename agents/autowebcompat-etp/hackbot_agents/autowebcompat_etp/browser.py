"""Download Firefox Nightly for the agent to drive."""

import logging
import platform
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import mozdownload
import mozinstall

logger = logging.getLogger("autowebcompat-etp")

# How many earlier days of Nightly to try if the latest build can't be fetched.
NIGHTLY_FALLBACK_DAYS = 3


def install_firefox_nightly() -> Path:
    install_dir = tempfile.mkdtemp(prefix="firefox-nightly-", dir=Path.home())

    # mozdownload doesn't correctly get arm builds for arm linux
    mozdownload_platform = (
        "linux-arm64" if platform.machine() in ("aarch64", "arm64") else None
    )

    logger.info("downloading Firefox nightly...")
    kwargs: dict[str, str] = {"branch": "mozilla-central"}
    scraper = None
    # The newest Nightly's folder is sometimes not yet listed on the archive,
    # in which case mozdownload raises NotFoundError; fall back to earlier days.
    for days_back in range(NIGHTLY_FALLBACK_DAYS + 1):
        if days_back:
            day = datetime.now(UTC).date() - timedelta(days=days_back)
            kwargs["date"] = day.isoformat()
            logger.warning("falling back to Firefox nightly from %s", day)
        try:
            scraper = mozdownload.FactoryScraper(
                "daily",
                platform=mozdownload_platform,
                destination=str(install_dir),
                **kwargs,
            )
            break
        except mozdownload.errors.NotFoundError:
            if days_back == NIGHTLY_FALLBACK_DAYS:
                raise
            logger.exception("could not find Firefox nightly build")
    archive = scraper.download()

    install_path = mozinstall.install(archive, str(install_dir))
    binary = Path(mozinstall.get_binary(install_path, "firefox"))

    logger.info("installed Firefox at %s", binary)
    return binary
