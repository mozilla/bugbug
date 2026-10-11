"""Install the Firefox build toolchain via `./mach bootstrap`."""

import asyncio
import os
import re
from pathlib import Path
from shutil import which
from typing import Any

APPLICATIONS = (
    "browser",
    "browser_artifact_mode",
    "mobile_android",
    "mobile_android_artifact_mode",
)


def _refresh_minidump_stackwalk() -> None:
    """Point ffpuppet at the minidump-stackwalk bootstrap just installed.

    ffpuppet resolves minidump-stackwalk on PATH once, when it is imported, which
    is before bootstrap has had a chance to install it; without a refresh crashes
    caught by evaluate_testcase come back without a symbolized stack.
    """
    try:
        from ffpuppet.minidump_parser import MinidumpParser
    except ImportError:
        return
    MinidumpParser.MDSW_BIN = which("minidump-stackwalk")


# Run with `./mach python`, for the tree's mozboot and mozbuild.
_MACH_PYTHON = Path(__file__).parent / "mach_python"


async def _install_developer_tools(firefox_dir: Path) -> tuple[int | None, str, str]:
    process = await asyncio.create_subprocess_exec(
        "./mach",
        "python",
        str(_MACH_PYTHON / "install_developer_tools.py"),
        cwd=firefox_dir,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode,
        stdout.decode("utf-8", errors="ignore"),
        stderr.decode("utf-8", errors="ignore"),
    )


def _version_key(path: Path) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", path.name))


def _link_android_tools() -> None:
    """Give the JDK and Android SDK tools bootstrap installed stable paths.

    They go in versioned directories (jdk/jdk-17.0.20.1+1, build-tools/37.0.0,
    cmdline-tools/23.0), so link the newest of each as `current` or `latest`
    (the SDK's own convention), for PATH to point at.
    """
    mozbuild = Path(os.environ.get("MOZBUILD_STATE_PATH", Path.home() / ".mozbuild"))
    sdk = mozbuild / "android-sdk-linux"
    for parent, link in (
        (mozbuild / "jdk", "current"),
        (sdk / "build-tools", "latest"),
        (sdk / "cmdline-tools", "latest"),
    ):
        versions = [
            p
            for p in parent.glob("*")
            if p.is_dir() and not p.is_symlink() and re.match(r"(jdk-)?\d", p.name)
        ]
        if not versions:
            continue
        target = parent / link
        target.unlink(missing_ok=True)
        target.symlink_to(max(versions, key=_version_key).name)


async def bootstrap_firefox(
    firefox_dir: Path, application: str = "browser"
) -> dict[str, Any]:
    """Run `./mach bootstrap` to install rust/clang/cbindgen for full builds.

    With ``application="mobile_android"`` (or ``"mobile_android_artifact_mode"``)
    it installs what Android builds need instead: a JDK, the Android SDK and NDK.

    Also installs the developer tools bootstrap offers coding agents
    (treeherder-cli, searchfox-cli, socorro-cli, stmo-cli, webspec-index);
    failing to is reported in the message but not an error.

    Required before a full (non-artifact) build can succeed. On a fresh
    image bootstrap takes ~10-15 min and downloads the toolchain into
    the running user's ~/.mozbuild/. Idempotent: re-runs are fast once
    the toolchain is in place.

    Args:
        firefox_dir: Firefox source directory (contains ./mach).
        application: What to bootstrap for, one of APPLICATIONS.

    Returns:
        Dict with success, message, stdout, stderr. Never raises.
    """
    try:
        if application not in APPLICATIONS:
            return {
                "success": False,
                "message": f"Unknown application {application!r}, expected one of "
                + ", ".join(APPLICATIONS),
                "stdout": "",
                "stderr": "",
            }
        if not firefox_dir.exists():
            return {
                "success": False,
                "message": f"Firefox directory not found at {firefox_dir}",
                "stdout": "",
                "stderr": "",
            }

        process = await asyncio.create_subprocess_exec(
            "./mach",
            "--no-interactive",
            "bootstrap",
            f"--application-choice={application}",
            cwd=firefox_dir,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Bootstrap tries to install system packages with `su` (without
            # sudo, as in the agent images), which asks for a password on the
            # controlling terminal. Without one it fails at once and bootstrap
            # carries on; with one (e.g. `docker run -t`) it would wait forever.
            start_new_session=True,
        )

        stdout, stderr = await process.communicate()
        stdout_output = stdout.decode("utf-8", errors="ignore") if stdout else ""
        stderr_output = stderr.decode("utf-8", errors="ignore") if stderr else ""

        if process.returncode == 0:
            _refresh_minidump_stackwalk()
            if application.startswith("mobile_android"):
                _link_android_tools()
            message = "mach bootstrap completed successfully"
            returncode, tools_stdout, tools_stderr = await _install_developer_tools(
                firefox_dir
            )
            if returncode != 0:
                message += (
                    f"; installing developer tools failed with exit code {returncode}"
                )
            return {
                "success": True,
                "message": message,
                "stdout": stdout_output + tools_stdout,
                "stderr": stderr_output + tools_stderr,
            }
        return {
            "success": False,
            "message": f"mach bootstrap failed with exit code {process.returncode}",
            "stdout": stdout_output,
            "stderr": stderr_output,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error running mach bootstrap: {type(e).__name__}: {e!s}",
            "stdout": "",
            "stderr": "",
        }
