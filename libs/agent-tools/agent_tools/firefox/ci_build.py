"""Build Firefox with the toolchains and mozconfig from a Linux CI task."""

import asyncio
import json
import platform
from pathlib import Path
from urllib.request import Request, urlopen

_TASK_URL = "https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/{task_id}"
_HEADERS = {"User-Agent": "hackbot-agent-tools/1.0"}
_TIMEOUT = 30


def _task(task_id: str) -> dict:
    request = Request(_TASK_URL.format(task_id=task_id), headers=_HEADERS)
    with urlopen(request, timeout=_TIMEOUT) as response:
        return json.load(response)


def linux_build_env(task_id: str) -> dict[str, str] | None:
    """Return a supported Linux build task's env, or ``None`` for other tasks.

    CI's toolchains only run on an x86-64 host. Fetches with a custom ``dest``
    or without extraction are not supported by the Firefox build tool.
    """
    if platform.machine() != "x86_64":
        return None

    task = _task(task_id)
    env = (task.get("payload") or {}).get("env") or {}
    if not (
        task.get("workerType", "").startswith("b-linux")
        and env.get("MOZCONFIG")
        and env.get("MOZ_FETCHES")
    ):
        return None

    fetches = json.loads(env["MOZ_FETCHES"])
    if any("dest" in fetch or not fetch.get("extract", True) for fetch in fetches):
        return None
    return env


async def prepare_ci_build(
    firefox_dir: Path,
    mozconfig_path: Path,
    objdir: Path,
    task_env: dict[str, str],
) -> dict[str, str]:
    """Fetch the CI toolchains and return env overrides for subsequent builds."""
    if platform.machine() != "x86_64":
        raise RuntimeError("CI Firefox toolchains require an x86-64 host")

    firefox_dir = firefox_dir.resolve()
    mozconfig_path = mozconfig_path.resolve()
    objdir = objdir.resolve()
    fetches = [
        f"{fetch['task']}:{fetch['artifact']}"
        for fetch in json.loads(task_env["MOZ_FETCHES"])
    ]
    fetches_dir = firefox_dir.parent / "fetches"
    fetches_dir.mkdir(exist_ok=True)

    # A reused workspace may contain a mozconfig from the last run. mach refuses
    # to fetch toolchains with that in-tree config unless automation is enabled.
    mozconfig_path.unlink(missing_ok=True)
    process = await asyncio.create_subprocess_exec(
        str(firefox_dir / "mach"),
        "--no-interactive",
        "artifact",
        "toolchain",
        "--from-task",
        *fetches,
        cwd=fetches_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"mach artifact toolchain failed with exit code {process.returncode}"
            + (f": {detail}" if detail else "")
        )

    overrides = {
        key: task_env[key]
        for key in ("DISABLE_CLANG_PLUGIN", "FORCE_GCC", "PERFHERDER_EXTRA_OPTIONS")
        if key in task_env
    }
    overrides.update(MOZ_AUTOMATION="1", MOZ_FETCHES_DIR=str(fetches_dir))
    mozconfig_path.write_text(
        f'. "$topsrcdir/{task_env["MOZCONFIG"]}"\nmk_add_options MOZ_OBJDIR={objdir}\n'
    )
    return overrides
