"""Install the Firefox build toolchain via `./mach bootstrap`."""

import asyncio
from pathlib import Path
from shutil import which
from typing import Any


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


async def bootstrap_firefox(firefox_dir: Path) -> dict[str, Any]:
    """Run `./mach bootstrap` to install rust/clang/cbindgen for full builds.

    Also installs the developer tools bootstrap offers coding agents
    (treeherder-cli, searchfox-cli, socorro-cli, stmo-cli, webspec-index);
    failing to is reported in the message but not an error.

    Required before a full (non-artifact) build can succeed. On a fresh
    image bootstrap takes ~10-15 min and downloads the toolchain into
    the running user's ~/.mozbuild/. Idempotent: re-runs are fast once
    the toolchain is in place.

    Args:
        firefox_dir: Firefox source directory (contains ./mach).

    Returns:
        Dict with success, message, stdout, stderr. Never raises.
    """
    try:
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
            "--application-choice=browser",
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
