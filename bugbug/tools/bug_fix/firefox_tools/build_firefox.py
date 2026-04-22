"""Build Firefox with a given mozconfig."""

import asyncio
import os
from pathlib import Path
from typing import Any


async def build_firefox(
    firefox_dir: Path,
    mozconfig_path: Path,
    objdir: Path,
) -> dict[str, Any]:
    """Build Firefox using the ASAN fuzzing configuration.

    Args:
        firefox_dir: Firefox source directory (contains ./mach)
        mozconfig_path: MOZCONFIG file to use
        objdir: Expected build output directory (reported back on success;
            mozconfig actually determines where the build lands, so this
            should match what the mozconfig sets)

    Returns:
        Dict with build result information (success, build_dir, message,
        stdout, stderr). Always returns a dict; never raises.
    """
    try:
        if not firefox_dir.exists():
            return {
                "success": False,
                "message": f"Firefox directory not found at {firefox_dir}",
            }

        if not mozconfig_path.exists():
            return {
                "success": False,
                "message": f"MOZCONFIG file not found at {mozconfig_path}",
            }

        env = os.environ.copy()
        env["MOZCONFIG"] = str(mozconfig_path.resolve())
        env["CLAUDECODE"] = "1"

        process = await asyncio.create_subprocess_exec(
            "./mach",
            "build",
            cwd=firefox_dir,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()
        stdout_output = stdout.decode("utf-8", errors="ignore") if stdout else ""
        stderr_output = stderr.decode("utf-8", errors="ignore") if stderr else ""

        if process.returncode == 0:
            return {
                "success": True,
                "build_dir": str(objdir),
                "message": "Firefox build completed successfully",
                "stdout": stdout_output,
                "stderr": stderr_output,
            }
        else:
            return {
                "success": False,
                "message": f"Firefox build failed with exit code {process.returncode}",
                "stdout": stdout_output,
                "stderr": stderr_output,
            }

    except Exception as e:
        error_msg = f"Error building Firefox: {type(e).__name__}: {e!s}"
        return {
            "success": False,
            "message": error_msg,
        }
