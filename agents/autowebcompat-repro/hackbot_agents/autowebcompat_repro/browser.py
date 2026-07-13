import logging
import platform
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

import mozdownload
import mozinstall
import requests

logger = logging.getLogger("autowebcompat-repro")

CHROME_VERSIONS_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)
CHROME_DOWNLOAD_TIMEOUT = 120
CHROME_CHUNK_SIZE = 1 << 20


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


def chrome_platform() -> str:
    """Chrome for Testing platform string for the current host."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        if machine not in {"x86_64", "amd64"}:
            raise RuntimeError(
                "Chrome for Testing has no linux build for "
                f"{platform.machine()}; only x86_64/amd64 is supported. Run the "
                "agent image as linux/amd64, e.g. DOCKER_DEFAULT_PLATFORM=linux/amd64."
            )
        return "linux64"
    if system == "Darwin":
        return "mac-arm64" if machine in {"arm64", "aarch64"} else "mac-x64"
    if system == "Windows":
        return "win64" if machine in {"x86_64", "amd64"} else "win32"
    raise RuntimeError(f"Unsupported platform for Chrome for Testing: {system}")


def resolve_chrome_download_url(channel: str, cft_platform: str) -> str:
    """Look up the Chrome for Testing download URL for a channel + platform."""
    response = requests.get(CHROME_VERSIONS_URL, timeout=CHROME_DOWNLOAD_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    entry = data["channels"][channel.capitalize()]
    logger.info("Chrome for Testing %s: version %s", channel, entry["version"])

    for download in entry["downloads"]["chrome"]:
        if download["platform"] == cft_platform:
            return download["url"]

    raise RuntimeError(
        f"no Chrome for Testing '{cft_platform}' download in {channel} channel"
    )


def install_chrome(channel: Literal["stable"] = "stable") -> Path:
    """Download Chrome for Testing and return the browser binary path."""
    cft_platform = chrome_platform()
    install_dir = Path(tempfile.mkdtemp(prefix=f"chrome-{channel}-", dir=Path.home()))

    url = resolve_chrome_download_url(channel, cft_platform)
    archive = install_dir / f"chrome-{cft_platform}.zip"

    logger.info("downloading Chrome for Testing from %s", url)
    with requests.get(url, stream=True, timeout=CHROME_DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        with archive.open("wb") as out:
            for chunk in response.iter_content(chunk_size=CHROME_CHUNK_SIZE):
                if chunk:
                    out.write(chunk)

    with zipfile.ZipFile(archive) as zf:
        zf.extractall(install_dir)
    archive.unlink()

    binary = install_dir / f"chrome-{cft_platform}" / "chrome"
    if not binary.exists():
        raise RuntimeError(f"Chrome binary not found at {binary} after unpacking")

    # zipfile does not preserve the executable bit; restore it.
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    logger.info("installed Chrome at %s", binary)
    return binary


class ChromeBrowsers:
    def __init__(self) -> None:
        self._stable: Path | None = None

    @property
    def stable(self) -> Path:
        if self._stable is None:
            self._stable = install_chrome(channel="stable")
        return self._stable
