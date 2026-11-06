"""Build Firefox with a given mozconfig."""

import asyncio
import os
from pathlib import Path
from typing import Any


async def build_firefox(
    firefox_dir: Path,
    mozconfig_path: Path,
    objdir: Path,
    target: str | None = None,
) -> dict[str, Any]:
    """Build Firefox using a specified mozconfig.

    Args:
        firefox_dir: Firefox source directory (contains ./mach)
        mozconfig_path: MOZCONFIG file to use
        objdir: Expected build output directory (reported back on success;
            mozconfig actually determines where the build lands, so this
            should match what the mozconfig sets)
        target: Optional build target (e.g. a directory like ``docshell/base``).
            When set, only that target is built -- far faster than a full tree
            build and enough to verify a localized fix compiles.

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

        # `mach bootstrap` installs rust under ~/.cargo/bin and clang under
        # ~/.mozbuild/clang/bin, neither of which is on the default PATH. Without
        # this the build fails with "Rust compiler not found" even right after a
        # successful bootstrap.
        home = Path.home()
        toolchain_bins = [home / ".cargo" / "bin", home / ".mozbuild" / "clang" / "bin"]
        env["PATH"] = os.pathsep.join(
            [*(str(p) for p in toolchain_bins), env.get("PATH", "")]
        )

        async def mach_build(*args: str) -> tuple[int | None, str, str]:
            process = await asyncio.create_subprocess_exec(
                "./mach",
                "build",
                # MOZ_AUTOMATION otherwise makes mach print the whole build log.
                "--quiet",
                *args,
                cwd=firefox_dir,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            return (
                process.returncode,
                stdout.decode("utf-8", errors="ignore") if stdout else "",
                stderr.decode("utf-8", errors="ignore") if stderr else "",
            )

        if target:
            # mach skips directory targets unless allowed (Bug 2014764). A
            # directory needs the generated headers, and the clang plugin when
            # configure enabled it, since every compile then loads the plugin.
            returncode, stdout_output, stderr_output = await mach_build(
                "pre-export", "export"
            )
            if returncode == 0:
                plugin_dir = objdir / "build" / "clang-plugin"
                plugin = ["build/clang-plugin"] if plugin_dir.is_dir() else []
                returncode, stdout_output, stderr_output = await mach_build(
                    "--allow-subdirectory-build", *plugin, target
                )
        else:
            returncode, stdout_output, stderr_output = await mach_build()

        if returncode == 0:
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
                "message": f"Firefox build failed with exit code {returncode}",
                "stdout": stdout_output,
                "stderr": stderr_output,
            }

    except Exception as e:
        error_msg = f"Error building Firefox: {type(e).__name__}: {e!s}"
        return {
            "success": False,
            "message": error_msg,
        }
