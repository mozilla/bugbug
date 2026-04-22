"""Evaluate testcase tool -- run a testcase in Firefox via grizzly, capture crash output."""

import asyncio
import tempfile
from logging import ERROR, getLogger
from pathlib import Path
from typing import Any

from grizzly.common.storage import TestCase
from grizzly.replay.replay import ReplayManager
from grizzly.target.firefox_target import FirefoxTarget
from prefpicker import PrefPicker
from sapphire import Sapphire

# Suppress grizzly's verbose logging (but allow CRITICAL and ERROR)
getLogger("grizzly").setLevel(ERROR)
getLogger("ffpuppet").setLevel(ERROR)
getLogger("sapphire").setLevel(ERROR)


class PIDCaptureFirefoxTarget(FirefoxTarget):
    """Firefox target that captures parent PID for crash analysis."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.parent_pid: int | None = None

    def launch(self, location: str) -> None:
        """Override to capture parent PID right after Firefox launches."""
        super().launch(location)
        # Capture parent PID immediately after launch
        if hasattr(self, "_puppet"):
            self.parent_pid = self._puppet.get_pid()


def _extract_crash_pid(crashdata: str) -> int | None:
    """Extract the PID of the crashing process from ASAN output.

    Args:
        crashdata: ASAN crash output

    Returns:
        PID of crashing process or None if not found
    """
    import re

    # ASAN format: ==PID==ERROR: AddressSanitizer: ...
    match = re.search(r"==(\d+)==ERROR:", crashdata)
    if match:
        return int(match.group(1))
    return None


def read_grizzly_logs(log_dir: Path) -> dict[str, str]:
    """Parse log files from a directory and categorize them.

    Args:
        log_dir: Directory containing log_*.txt files

    Returns:
        Dict with keys: stderr, stdout, crashdata
    """
    logs = {"stderr": "", "stdout": "", "crashdata": ""}

    for log_path in log_dir.glob("log_*.txt"):
        with open(log_path, errors="ignore") as f:
            log_content = f.read()
            log_name = log_path.name.lower()
            if "stderr" in log_name:
                logs["stderr"] += log_content
            elif "stdout" in log_name:
                logs["stdout"] += log_content
            else:
                logs["crashdata"] += log_content

    return logs


async def evaluate_testcase(
    content: str,
    filename: str,
    firefox_binary: Path,
    timeout: int = 30,
    prefs: dict[str, str | int | bool] = {},
) -> dict[str, Any]:
    """Test a testcase in Firefox and capture crash output.

    Args:
        content: Testcase file content
        filename: Name for the testcase file
        firefox_binary: Path to Firefox binary
        timeout: Timeout in seconds (grizzly's crash-wait)
        prefs: Optional custom Firefox preferences to set

    Returns:
        Dict with crash information (crashed, message, files, logs, etc.)
        Always returns a dict; never raises.
    """
    # grizzly / Sapphire / PrefPicker / ffpuppet are all synchronous. Running
    # them on the event loop thread starves the Agent SDK's message reader —
    # the MCP response can't be written back and the agent transcript stops
    # mid-stream. Push the whole thing onto a worker thread.
    #
    # Outer deadline is generous: grizzly's own ``timeout`` is the crash-wait,
    # but launch, pref generation, and teardown are unbounded in grizzly. If
    # ffpuppet prints "Launch failed" and then hangs inside Sapphire, this is
    # what gets us out. The thread itself will keep running after a timeout
    # (threads can't be cancelled), but the tool returns and the agent moves
    # on — a leaked thread is better than a frozen agent.
    outer_deadline = timeout + 90
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _run_testcase_in_browser,
                content,
                filename,
                firefox_binary,
                timeout,
                prefs,
            ),
            timeout=outer_deadline,
        )
    except asyncio.TimeoutError:
        return {
            "crashed": False,
            "message": (
                f"evaluate_testcase exceeded {outer_deadline}s — grizzly "
                f"likely hung (launch failure or Sapphire stuck). The Firefox "
                f"binary at {firefox_binary} may be broken; try build_firefox."
            ),
        }
    except Exception as e:
        error_msg = f"Error running Firefox with grizzly: {type(e).__name__}: {e!s}"
        return {
            "crashed": False,
            "message": error_msg,
        }


def _format_pref_value(value: str | int | bool) -> str:
    """Format a preference value for prefs.js."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    # String: quote it
    return f'"{value}"'


def _run_testcase_in_browser(
    content: str,
    filename: str,
    firefox_binary: Path,
    timeout: int = 30,
    prefs: dict[str, str | int | bool] = {},
) -> dict[str, Any]:
    """Internal: run testcase in Firefox via grizzly replay.

    Synchronous — called via ``asyncio.to_thread`` from the async wrapper.
    """
    if not firefox_binary.exists():
        return {
            "crashed": False,
            "message": f"Firefox binary not found at {firefox_binary}",
        }

    testcase = TestCase(
        entry_point=filename, adapter_name="larrey", input_fname=filename
    )

    # Add testcase content from bytes (creates temp file internally)
    testcase.add_from_bytes(content.encode("utf-8"), filename, required=True)

    # Use our custom target to capture parent PID
    target = LarreyFirefoxTarget(
        binary=firefox_binary,
        display_mode="xvfb",
        launch_timeout=30,
        log_limit=0,
        memory_limit=0,
    )

    # Enable verbose logging
    target.environ["MOZ_LOG"] = "ConsoleAPI:5,PageMessages:5"
    target.environ["GNOME_ACCESSIBILITY"] = "1"

    prefs["devtools.console.stdout.content"] = True

    # If custom prefs are provided, generate base prefs.js with prefpicker
    # and append custom prefs before process_assets() so grizzly uses ours
    if prefs:
        with tempfile.TemporaryDirectory(prefix="larrey_prefs_") as prefs_dir:
            prefs_path = Path(prefs_dir) / "prefs.js"
            template = PrefPicker.lookup_template("browser-fuzzing.yml")
            assert template is not None
            PrefPicker.load_template(template).create_prefsjs(prefs_path)
            # Append custom prefs
            with open(prefs_path, "a") as f:
                f.write("\n// Custom larrey prefs\n")
                for name, value in prefs.items():
                    f.write(f'user_pref("{name}", {_format_pref_value(value)});\n')
            target.asset_mgr.add("prefs", prefs_path)

    # Process assets (prefs, etc.) - required for Firefox to launch properly
    target.process_assets()

    results = []
    try:
        with Sapphire(auto_close=1) as server:
            target.reverse(server.port, server.port)
            with ReplayManager(
                ignore=frozenset(),
                server=server,
                target=target,
                any_crash=True,
                use_harness=False,
            ) as replay:
                results = replay.run(
                    testcases=[testcase],
                    time_limit=timeout,
                    expect_hang=False,
                )

        if not results:
            # No crash - capture logs for debugging
            with tempfile.TemporaryDirectory(prefix="larrey_logs_") as log_dir_str:
                log_dir = Path(log_dir_str)
                target.save_logs(log_dir)
                logs = read_grizzly_logs(log_dir)

                # Remove crashdata key since there's no crash
                logs.pop("crashdata", None)

                msg = (
                    "No crash detected - check logs for clues "
                    "about why the testcase didn't trigger the vulnerability"
                )
                return {
                    "crashed": False,
                    "message": msg,
                    "logs": logs,
                }

        # Crash detected! Dump testcase and return file contents
        result_obj = results[0]
        report = result_obj.report

        # Create temp directory for dump
        with tempfile.TemporaryDirectory(prefix="larrey_dump_") as dump_dir_str:
            dump_dir = Path(dump_dir_str)

            # Dump testcase to temp directory
            testcase.dump(dump_dir, include_details=True)

            # Collect all files from dump
            files = {}
            for file_path in dump_dir.rglob("*"):
                if file_path.is_file():
                    relative_name = file_path.relative_to(dump_dir)
                    with open(file_path, errors="ignore") as f:
                        files[str(relative_name)] = f.read()

            # Collect crash logs
            logs = read_grizzly_logs(report.path)

            # Determine if parent or content process crashed
            crashed_parent = False
            parent_pid = target.parent_pid
            crash_pid = _extract_crash_pid(logs.get("crashdata", ""))

            if parent_pid is not None and crash_pid is not None:
                crashed_parent = crash_pid == parent_pid

            return {
                "crashed": True,
                "crashed_parent": crashed_parent,
                "files": files,
                "logs": logs,
                "message": "Crash detected",
            }

    finally:
        testcase.cleanup()
        target.cleanup()
        for result_obj in results:
            result_obj.report.cleanup()
