import logging
import os
import platform
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Literal

import mozdownload
import mozinstall
import requests

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
    versions_url = (
        "https://googlechromelabs.github.io/chrome-for-testing/"
        "last-known-good-versions-with-downloads.json"
    )
    response = requests.get(versions_url, timeout=120)
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


def chrome_binary_path(install_dir: Path, cft_platform: str) -> Path:
    """Path to the Chrome for Testing binary within the unpacked archive.

    Chrome for Testing uses a different executable name/layout per platform:
    on macOS it is inside an `.app` bundle, on Windows it is `chrome.exe`,
    and on Linux it is a `chrome`.
    """
    package = install_dir / f"chrome-{cft_platform}"
    if cft_platform.startswith("mac"):
        return (
            package
            / "Google Chrome for Testing.app"
            / "Contents"
            / "MacOS"
            / "Google Chrome for Testing"
        )
    if cft_platform.startswith("win"):
        return package / "chrome.exe"
    return package / "chrome"


def unzip(archive: Path, dest: Path) -> None:
    """Extract a zip, preserving unix permission bits and recreating symlinks.

    This keeps the Chrome binary executable and, on macOS, keeps the ``.app``
    bundle's internal symlinks intact.

    Adapted from wpt's tools/wpt/utils.py::unzip.
    """
    with zipfile.ZipFile(archive) as zip_data:
        for info in zip_data.infolist():
            # external_attr's two high bytes carry the unix st_mode, but only
            # when the archive was created on a unix system (create_system == 3).
            # A DOS/Windows-created archive, or extraction on Windows, carries no
            # useful permission info, so fall back to a plain extract there.
            if info.create_system == 0 or sys.platform == "win32":
                zip_data.extract(info, path=dest)
                continue

            st_mode = info.external_attr >> 16
            dst_path = os.path.join(dest, info.filename)
            if stat.S_ISLNK(st_mode):
                # Symlinks are stored as files whose contents are the target;
                # recreate the link rather than extracting it as a file.
                link_target = zip_data.read(info)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                if os.path.islink(dst_path):
                    os.unlink(dst_path)
                os.symlink(link_target, dst_path)
            else:
                zip_data.extract(info, path=dest)
                # Preserve the permission bits (rwxrwxrwx) only, dropping the
                # sticky/setuid/setgid bits.
                os.chmod(dst_path, st_mode & 0o777)


def install_chrome(channel: Literal["stable"] = "stable") -> Path:
    """Download Chrome for Testing and return the browser binary path."""
    cft_platform = chrome_platform()
    install_dir = Path(tempfile.mkdtemp(prefix=f"chrome-{channel}-", dir=Path.home()))

    url = resolve_chrome_download_url(channel, cft_platform)
    archive = install_dir / f"chrome-{cft_platform}.zip"

    logger.info("downloading Chrome for Testing from %s", url)
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with archive.open("wb") as out:
            for chunk in response.iter_content(chunk_size=1 << 20):
                if chunk:
                    out.write(chunk)

    unzip(archive, install_dir)
    archive.unlink()

    binary = chrome_binary_path(install_dir, cft_platform)
    if not binary.exists():
        raise RuntimeError(f"Chrome binary not found at {binary} after unpacking")

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
