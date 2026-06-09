"""Install the Firefox build toolchain via `./mach bootstrap`."""

import asyncio
from pathlib import Path
from typing import Any


async def bootstrap_firefox(firefox_dir: Path) -> dict[str, Any]:
    """Run `./mach bootstrap` to install rust/clang/cbindgen for full builds.

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
            "bootstrap",
            "--no-interactive",
            "--application-choice=browser",
            cwd=firefox_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()
        stdout_output = stdout.decode("utf-8", errors="ignore") if stdout else ""
        stderr_output = stderr.decode("utf-8", errors="ignore") if stderr else ""

        if process.returncode == 0:
            return {
                "success": True,
                "message": "mach bootstrap completed successfully",
                "stdout": stdout_output,
                "stderr": stderr_output,
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
