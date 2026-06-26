"""Download and install a prebuilt Firefox Nightly for the agent to drive."""

from __future__ import annotations

import logging
import platform
import shutil
from pathlib import Path

import mozdownload
import mozinstall

INSTALL_DIR = Path.home() / "firefox"
BRANCH = "mozilla-central"

logger = logging.getLogger("test-plan-generator")


def install_firefox_nightly() -> Path:
    mozdownload_platform = (
        "linux-arm64" if platform.machine() in ("aarch64", "arm64") else None
    )

    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
    INSTALL_DIR.mkdir(parents=True)

    logger.info("downloading Firefox Nightly...")
    scraper = mozdownload.FactoryScraper(
        "daily",
        branch=BRANCH,
        platform=mozdownload_platform,
        destination=str(INSTALL_DIR),
    )
    archive = scraper.download()

    install_folder = mozinstall.install(archive, str(INSTALL_DIR))
    binary = Path(mozinstall.get_binary(install_folder, "firefox"))

    logger.info("installed Firefox at %s", binary)
    return binary
