"""Run Firefox tests with a test harness and collect per-test results."""

import asyncio
import json
import os
from pathlib import Path

# Statuses a harness reports for a test, or a subtest, that passed.
PASSING = frozenset({"PASS", "OK"})
# Where each harness keeps its tests and expectations in the tree.
TEST_ROOTS = {"wpt": "testing/web-platform"}
# Where the test paths a caller passes are relative to.
TEST_DIRS = {"wpt": "testing/web-platform/tests"}


def wptreport_results(report: dict) -> dict[str, dict]:
    """Each test's status and its subtests' statuses, from a wptreport."""
    return {
        test["test"]: {
            "status": test["status"],
            "subtests": {
                sub["name"]: sub["status"] for sub in test.get("subtests", [])
            },
        }
        for test in report.get("results", [])
    }


async def run_tests(
    firefox_dir: Path,
    harness: str,
    paths: list[str],
    report_path: Path,
    prefs: dict[str, object] | None = None,
    timeout: float = 3600,
) -> dict[str, dict]:
    """Run ``paths`` with ``harness`` against the built tree.

    Args:
        firefox_dir: Firefox source directory (contains ./mach), already built.
        harness: Test harness; only ``"wpt"`` is supported.
        paths: Test paths, relative to the harness's test directory
            (``TEST_DIRS``).
        report_path: Where the harness writes its machine-readable report; its
            output goes next to it, with a ``.log`` suffix.
        prefs: Prefs to set for the run.
        timeout: Seconds before the run is abandoned.

    Returns:
        ``{test: {"status": ..., "subtests": {name: status}}}``; empty when the
        harness produced no report (see the log). Never raises for a failing run.
    """
    if harness not in TEST_ROOTS:
        raise ValueError(f"unsupported test harness {harness!r}")
    args = ["./mach", "wpt", "--headless", "--log-wptreport", str(report_path)]
    for name, value in (prefs or {}).items():
        text = str(value).lower() if isinstance(value, bool) else str(value)
        args += ["--setpref", f"{name}={text}"]
    with report_path.with_suffix(".log").open("w") as log:
        process = await asyncio.create_subprocess_exec(
            *args,
            *(f"{TEST_DIRS[harness]}/{path}" for path in paths),
            cwd=firefox_dir,
            env=os.environ.copy(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
    try:
        return wptreport_results(json.loads(report_path.read_text()))
    except OSError, ValueError:
        return {}
