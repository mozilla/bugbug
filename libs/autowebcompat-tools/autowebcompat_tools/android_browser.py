from __future__ import annotations

import json
import logging
import socket
import tempfile
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from urllib.request import urlopen

import mozdownload

from . import android_emulator

logger = logging.getLogger(__name__)
FIREFOX_MARIONETTE_PORT = 2829
FIREFOX_BIDI_PORT = 9223
CHROME_DEVTOOLS_PORT = 9222
CHROME_PACKAGE = "com.android.chrome"
CHROME_SOCKET = "chrome_devtools_remote"
FENIX_PACKAGES = {"nightly": "org.mozilla.fenix", "stable": "org.mozilla.firefox"}


def prepare_chrome() -> None:
    """Configure the mobile Chrome preinstalled in the Play Store image."""
    # Select Chrome as the debug app so it reads our startup flags,
    # which skip welcome screens and configure its remote debugging socket
    android_emulator.adb("shell", "am", "set-debug-app", "--persistent", CHROME_PACKAGE)
    android_emulator.adb(
        "shell",
        "echo 'chrome --disable-fre --no-first-run --no-default-browser-check "
        f"--remote-debugging-socket-name={CHROME_SOCKET}' "
        "> /data/local/tmp/chrome-command-line",
    )
    android_emulator.adb("shell", "chmod", "644", "/data/local/tmp/chrome-command-line")


def wait_for_firefox(timeout: int = 120) -> None:
    """Wait for the Marionette greeting, without creating a session."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # adb forward accepts the connection even when nothing listens on the
        # device, then closes it, so only a greeting proves Firefox is ready.
        try:
            with socket.create_connection(
                ("127.0.0.1", FIREFOX_MARIONETTE_PORT), timeout=2
            ) as connection:
                greeting = b""
                while b'"applicationType":"gecko"' not in greeting:
                    chunk = connection.recv(256)
                    if not chunk or len(greeting) > 4096:
                        break
                    greeting += chunk
                else:
                    logger.info("Firefox Marionette is ready for MCP attachment")
                    return
        except OSError:
            pass
        time.sleep(1)
    raise android_emulator.AndroidError(
        f"Firefox Marionette did not become ready within {timeout}s"
    )


def chrome_ready(port: int = CHROME_DEVTOOLS_PORT) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
            version = json.load(response)
    except OSError, ValueError:
        return False
    if not version.get("webSocketDebuggerUrl"):
        return False
    logger.info("%s DevTools ready on port %s", version.get("Browser"), port)
    return True


def forward_chrome_devtools(port: int = CHROME_DEVTOOLS_PORT) -> None:
    """Expose Chrome's on-device DevTools socket on a host port."""
    android_emulator.adb("forward", f"tcp:{port}", f"localabstract:{CHROME_SOCKET}")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if chrome_ready(port):
            return
        time.sleep(1)
    raise android_emulator.AndroidError(
        f"Chrome DevTools on port {port} did not become ready"
    )


def install_fenix(channel: str) -> None:
    package = FENIX_PACKAGES[channel]
    if android_emulator.adb("shell", "pm", "path", package, check=False).strip():
        return
    with tempfile.TemporaryDirectory(prefix="fenix-") as tmp:
        options = (
            {"branch": "mozilla-central"}
            if channel == "nightly"
            else {"version": "latest"}
        )
        scraper = mozdownload.FactoryScraper(
            "daily" if channel == "nightly" else "release",
            application="fenix",
            platform="android-x86_64",
            destination=tmp,
            **options,
        )
        android_emulator.install_apk(Path(scraper.download()))


class AndroidBrowser(ABC):
    name: ClassVar[str]
    ports: ClassVar[tuple[int, ...]]

    @property
    @abstractmethod
    def package(self) -> str: ...

    @property
    @abstractmethod
    def endpoint(self) -> str: ...

    @abstractmethod
    def start(self, *, fresh: bool = False) -> None: ...

    @abstractmethod
    def is_ready(self) -> bool: ...

    def __str__(self) -> str:
        return f"android:{self.name}"

    @staticmethod
    def parse(value: str) -> AndroidBrowser:
        _, name, *channel = value.split(":")
        return AndroidChrome() if name == "chrome" else AndroidFirefox(*channel)

    def is_alive(self) -> bool:
        return bool(
            android_emulator.adb("shell", "pidof", self.package, check=False).strip()
        )

    def activate(self) -> None:
        """Bring a browser forward, restoring automation if it stopped working."""
        if not self.is_ready():
            # The other browser may have replaced Android's global debug-app
            # selection. Restore it before relaunching so automation flags load.
            self.start()
            return
        android_emulator.start_app(self.package)

    def stop(self) -> None:
        android_emulator.adb("shell", "am", "force-stop", self.package)
        for port in self.ports:
            android_emulator.adb("forward", "--remove", f"tcp:{port}", check=False)


@dataclass(frozen=True)
class AndroidFirefox(AndroidBrowser):
    channel: str = "stable"
    name: ClassVar[str] = "firefox"
    ports: ClassVar[tuple[int, ...]] = (FIREFOX_MARIONETTE_PORT, FIREFOX_BIDI_PORT)

    def __str__(self) -> str:
        return f"{super().__str__()}:{self.channel}"

    @property
    def package(self) -> str:
        return FENIX_PACKAGES[self.channel]

    @property
    def endpoint(self) -> str:
        return f"ws://127.0.0.1:{FIREFOX_BIDI_PORT}/session"

    def is_ready(self) -> bool:
        return self.is_alive()

    def start(self, *, fresh: bool = False) -> None:
        """Launch a browser with automation enabled.

        ``fresh`` wipes Firefox's app data first, giving it a clean profile.
        Relaunches within a task keep the profile.
        """
        install_fenix(self.channel)
        # Nightly and release use the same device ports. Stopping only the
        # requested package lets a previous channel answer the new connection.
        channels = [AndroidFirefox(channel) for channel in FENIX_PACKAGES]
        for firefox in channels:
            firefox.stop()
        # force-stop can return before the process exits; a survivor would
        # answer on the shared ports and the run would test the wrong channel.
        deadline = time.monotonic() + 10
        while running := [
            firefox.package for firefox in channels if firefox.is_alive()
        ]:
            if time.monotonic() > deadline:
                raise android_emulator.AndroidError(
                    f"Firefox packages did not stop: {running}"
                )
            time.sleep(0.2)
        if fresh:
            output = android_emulator.adb("shell", "pm", "clear", self.package)
            if "Success" not in output:
                raise android_emulator.AndroidError(
                    f"pm clear {self.package} failed: {output.strip()}"
                )
        logger.info("Starting Firefox %s: package=%s", self.channel, self.package)
        config = {
            "env": {"MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"},
            "args": [
                "--marionette",
                "--remote-debugging-port",
                str(FIREFOX_BIDI_PORT),
                "--remote-allow-hosts",
                "localhost,127.0.0.1",
            ],
            "prefs": {"marionette.port": FIREFOX_MARIONETTE_PORT},
        }

        destination = f"/data/local/tmp/{self.package}-geckoview-config.yaml"
        android_emulator.adb("shell", f"echo '{json.dumps(config)}' > {destination}")
        android_emulator.adb("shell", "chmod", "644", destination)
        android_emulator.adb(
            "shell", "am", "set-debug-app", "--persistent", self.package
        )
        for port in self.ports:
            android_emulator.adb("forward", f"tcp:{port}", f"tcp:{port}")
        android_emulator.am_start(
            "-n",
            f"{self.package}/org.mozilla.fenix.IntentReceiverActivity",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            "about:blank",
            "--ez",
            "automationtest",
            "true",
            "--ez",
            "performancetest",
            "true",
        )
        wait_for_firefox()


@dataclass(frozen=True)
class AndroidChrome(AndroidBrowser):
    name: ClassVar[str] = "chrome"
    ports: ClassVar[tuple[int, ...]] = (CHROME_DEVTOOLS_PORT,)

    @property
    def package(self) -> str:
        return CHROME_PACKAGE

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{CHROME_DEVTOOLS_PORT}"

    def is_ready(self) -> bool:
        return chrome_ready()

    def start(self, *, fresh: bool = False) -> None:
        prepare_chrome()
        self.stop()
        android_emulator.start_app(self.package, "about:blank")
        forward_chrome_devtools()


class AndroidFirefoxBrowsers:
    nightly = AndroidFirefox("nightly")
    stable = AndroidFirefox("stable")


class AndroidChromeBrowsers:
    stable = AndroidChrome()


@contextmanager
def AndroidEnvironment(logcat: Path | None = None):
    with android_emulator.emulator(logcat):
        yield AndroidFirefoxBrowsers(), AndroidChromeBrowsers()
