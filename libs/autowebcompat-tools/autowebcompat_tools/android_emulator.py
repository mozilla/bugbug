from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from pathlib import Path

logger = logging.getLogger(__name__)
AVD_NAME = "webcompat"
EMULATOR_LOG = Path("/tmp/android-emulator.log")
DEVICE_SERIAL = "emulator-5554"
BOOT_TIMEOUT = 300
ADB_TIMEOUT = 120


def available() -> bool:
    return (
        shutil.which("emulator") is not None
        and shutil.which("adb") is not None
        and os.access("/dev/kvm", os.R_OK | os.W_OK)
    )


class AndroidError(Exception):
    """Raised when the emulator or a device operation fails."""


def adb(*args: str, check: bool = True, timeout: int = ADB_TIMEOUT) -> str:
    proc = subprocess.run(
        ["adb", "-s", DEVICE_SERIAL, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise AndroidError(
            f"adb {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def getprop(name: str) -> str:
    return adb("shell", "getprop", name, check=False).strip()


def install_apk(apk: Path) -> None:
    """Install an APK, allowing replacement and downgrades."""
    logger.info("installing %s", apk.name)
    adb("install", "-r", "-d", str(apk), timeout=600)


@cache
def launcher_activity(package: str) -> str:
    """Resolve a package's launcher component; ``am start`` needs it explicitly."""
    output = adb(
        "shell",
        "cmd",
        "package",
        "query-activities",
        "--components",
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
        "-p",
        package,
    )
    for line in output.split():
        if line.startswith(f"{package}/"):
            return line
    raise AndroidError(f"No launcher activity found for {package}: {output.strip()}")


def am_start(*args: str) -> None:
    """Start an activity and wait until it has launched."""
    output = adb("shell", "am", "start", "-W", *args)
    if "Error:" in output or "Error type" in output:
        raise AndroidError(f"am start {' '.join(args)} failed: {output.strip()}")


def start_app(package: str, url: str | None = None) -> None:
    """Launch an app or bring it to the foreground, optionally at a URL."""
    intent = (
        ["-a", "android.intent.action.VIEW", "-d", url]
        if url is not None
        else [
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
        ]
    )
    am_start("-n", launcher_activity(package), *intent)


def wait_for_boot(process: subprocess.Popen, timeout: int = BOOT_TIMEOUT) -> None:
    """Block until the device reports a completed boot."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AndroidError(
                f"emulator exited with {process.returncode} before booting"
            )
        if getprop("sys.boot_completed") == "1":
            logger.info(
                "emulator booted: Android %s, %s",
                getprop("ro.build.version.release"),
                getprop("ro.product.cpu.abi"),
            )
            return
        time.sleep(5)
    raise AndroidError(
        f"emulator did not boot within {timeout}s; adb devices: "
        f"{adb('devices', check=False, timeout=10).strip()}"
    )


@contextmanager
def emulator(logcat: Path | None = None) -> Iterator[None]:
    """Boot the baked AVD for the duration of the block."""
    # Starting ADB first creates its user key (ADB_VENDOR_KEYS in the image)
    # before the emulator injects trusted public keys into the guest.
    subprocess.run(["adb", "start-server"], check=True, capture_output=True, timeout=30)
    cmd = [
        "emulator",
        "-avd",
        AVD_NAME,
        "-no-boot-anim",
        "-no-snapshot",
        "-accel",
        "on",
        "-gpu",
        "swiftshader",
        "-camera-back",
        "emulated",
        "-camera-front",
        "emulated",
    ]
    logger.info("starting emulator %s", AVD_NAME)
    with EMULATOR_LOG.open("w") as log:
        process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    try:
        try:
            wait_for_boot(process)
        except Exception:
            logger.error(
                "Emulator log tail:\n%s",
                EMULATOR_LOG.read_text(errors="replace")[-4000:],
            )
            raise
        yield
    finally:
        if logcat is not None and process.poll() is None:
            try:
                logcat.write_text(adb("logcat", "-d", check=False, timeout=60))
            except (subprocess.SubprocessError, OSError) as e:
                logger.warning("Could not save logcat: %s", e)
        if process.poll() is None:
            # Graceful shutdown when ADB can reach the device; never let an
            # ADB failure skip the process cleanup or mask the original error.
            try:
                adb("emu", "kill", check=False, timeout=30)
            except subprocess.SubprocessError, OSError:
                process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
