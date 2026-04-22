"""Evaluate testcase tool for testing vulnerabilities in the SpiderMonkey JS shell."""

import asyncio
import os
import signal
import tempfile
from pathlib import Path
from typing import Any

MAX_LOG_SIZE = 1_048_576  # bytes; logs are tail-truncated to this limit


async def js_shell_evaluator(
    content: str,
    js_binary: Path,
    timeout: int = 30,
    flags: list[str] | None = None,
) -> dict[str, Any]:
    """Execute a testcase in the SpiderMonkey JS shell and capture crash output.

    Args:
        content: Testcase content.
        js_binary: Path to SpiderMonkey binary.
        timeout: Optional timeout in seconds.
        flags: Optional list of runtime flags to pass to the JS shell
            (e.g. ["--no-jit"])

    Returns:
        Dict with crash information (crashed, message, logs, files).
        Log values are tail-truncated to 1 MB if the output is large.
        Always returns a dict; never raises.
    """
    if not js_binary.exists():
        return {
            "crashed": False,
            "message": f"JS shell binary not found at {js_binary}",
        }

    try:
        with tempfile.TemporaryDirectory(prefix="larrey_js_") as tmp_dir:
            fd, tmp_path = tempfile.mkstemp(suffix=".js", dir=tmp_dir)
            testcase_path = Path(tmp_path)
            os.close(fd)
            testcase_path.write_text(content, encoding="utf-8")

            proc = await asyncio.create_subprocess_exec(
                str(js_binary),
                "--fuzzing-safe",
                *(flags or []),
                str(testcase_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ},
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return {
                    "crashed": False,
                    "message": f"Timed out after {timeout}s — no crash detected",
                    "logs": {"stderr": "", "stdout": ""},
                }

            stdout_raw = stdout_bytes.decode("utf-8", errors="replace")
            stderr_raw = stderr_bytes.decode("utf-8", errors="replace")
            stdout = (
                stdout_raw[-MAX_LOG_SIZE:]
                if len(stdout_raw) > MAX_LOG_SIZE
                else stdout_raw
            )
            stderr = (
                stderr_raw[-MAX_LOG_SIZE:]
                if len(stderr_raw) > MAX_LOG_SIZE
                else stderr_raw
            )
            exit_code = proc.returncode

            # Detect crash: killed by signal (negative exit code) or ASAN in stderr
            killed_by_signal = exit_code is not None and exit_code < 0
            has_asan = (
                "AddressSanitizer" in stderr or "ERROR: AddressSanitizer" in stderr
            )

            crashed = killed_by_signal or has_asan

            if not crashed:
                msg = (
                    f"JS shell exited with code {exit_code} (JS error, not a crash)"
                    if exit_code != 0
                    else "No crash detected"
                )
                return {
                    "crashed": False,
                    "message": msg,
                    "logs": {"stderr": stderr, "stdout": stdout},
                }

            signal_name = ""
            if killed_by_signal and exit_code is not None:
                sig_num = -exit_code
                try:
                    sig = signal.Signals(sig_num)
                    signal_name = f" (signal {sig.name})"
                except ValueError:
                    signal_name = f" (signal {sig_num})"

            return {
                "crashed": True,
                "message": f"Crash detected{signal_name}",
                "files": {testcase_path.name: content},
                "logs": {
                    "stderr": stderr,
                    "stdout": stdout,
                    "crashdata": stderr,  # ASAN output goes to stderr
                },
            }
    except Exception as e:
        return {
            "crashed": False,
            "message": f"Error running JS shell: {type(e).__name__}: {e!s}",
        }
