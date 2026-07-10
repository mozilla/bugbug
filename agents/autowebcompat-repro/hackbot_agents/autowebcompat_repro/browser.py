import logging
import platform
import tempfile
from pathlib import Path
from typing import Literal

import mozdownload
import mozinstall

logger = logging.getLogger("autowebcompat-repro")


def install_firefox(
    channel: Literal["nightly"] | Literal["stable"] | Literal["esr"],
) -> Path:
    install_dir = tempfile.mkdtemp(prefix=f"firefox-{channel}-", dir=Path.home())

    # mozdownload doesn't correctly get arm builds for arm linux
    mozdownload_platform = (
        "linux-arm64" if platform.machine() in ("aarch64", "arm64") else None
    )

    kwargs = {}
    if channel == "nightly":
        scraper_type = "daily"
        kwargs["branch"] = "mozilla-central"
    else:
        scraper_type = "release"
        if channel == "stable":
            version = "latest"
        else:
            assert channel == "esr"
            version = "latest-esr"
        kwargs["version"] = version

    logger.info("downloading Firefox %s...", channel)
    scraper = mozdownload.FactoryScraper(
        scraper_type,
        platform=mozdownload_platform,
        destination=str(install_dir),
        **kwargs,
    )
    archive = scraper.download()

    install_path = mozinstall.install(archive, str(install_dir))
    binary = Path(mozinstall.get_binary(install_path, "firefox"))

    logger.info("installed Firefox at %s", binary)
    return binary


class FirefoxBrowsers:
    def __init__(self) -> None:
        self._nightly: Path | None = None
        self._esr: Path | None = None
        self._stable: Path | None = None

    @property
    def nightly(self) -> Path:
        if self._nightly is None:
            self._nightly = install_firefox(channel="nightly")
        return self._nightly

    @property
    def stable(self) -> Path:
        if self._stable is None:
            self._stable = install_firefox(channel="stable")
        return self._stable

    @property
    def esr(self) -> Path:
        if self._esr is None:
            self._esr = install_firefox(channel="esr")
        return self._esr
