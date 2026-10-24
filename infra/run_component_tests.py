import os
import subprocess
from pathlib import Path

from logging import INFO, basicConfig, getLogger

basicConfig(level=INFO)
logger = getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

COMPONENTS = {
    "services":
    {
        "services/hackbot-api": "app",
        "services/hackbot-pulse-listener": "app",
        "services/mcp": "bugbug_mcp"
    },
    "agents":
    {
        "agents/bug-fix": "hackbot_agents",
        "agents/build-repair": "hackbot_agents",
        "agents/frontend-triage": "hackbot_agents",
        "agents/test-repair": "hackbot_agents",
        "agents/uplift-resolve": "hackbot_agents"
    }
}

### TODO?
GLOBAL_FILES = {
    ".taskcluster.yml",
    "pyproject.toml",
    "uv.lock",
    "infra/run_component_tests.py"
}


def get_changed_files_paths(base_sha=None, base_repo=None):
    """ TODO """

    if not base_sha:
        return None

    try:
        if base_repo:
            subprocess.run(
                ["git", "fetch", "--no-tags", "--", base_repo, base_sha],
                cwd=ROOT,
                check=True)

        merge_base = subprocess.check_output(
            ["git", "merge-base", base_sha, "HEAD"], cwd=ROOT
        ).decode().strip()

        paths = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", merge_base, "HEAD", "--"],
            cwd=ROOT)
        return { os.fsdecode(path) for path in paths.split(b"\0") if path }

    except (OSError, subprocess.CalledProcessError) as exc:
        logger.warning("Cannot determine changes; running all tests: %s", exc)
        return None


def select_components(group, changed_files):
    """ TODO """

    components = COMPONENTS[group]
    if (
        changed_files is None
        or changed_files & GLOBAL_FILES
        or any(path.startswith("libs/") for path in changed_files)
    ):
        return list(components)

    selected_components = []

    for component in components:
        for path in changed_files:
            if (path.startswith(component + "/") or (
                component == "services/mcp" and path.startswith("bugbug/")
            )):
                selected_components.append(component)
                break

    return selected_components


def run_tests(group, components):
    """ TODO """

    if not components:
        logger.info("No base commit provided. Running all tests.")
        return

    for component in components:

        command = ["uv", "run", "--locked"]
        dependencies = ["pytest==9.1.0", "pytest-cov==7.1.0", "pytest-asyncio==1.4.0"]

        if component == "services/mcp":
            dependencies.append("pytest-mock==3.15.1")

        for dependency in dependencies:
            command.extend(["--with", dependency])

        command.extend(["pytest", f"--cov={COMPONENTS[group][component]}", "--cov-append", "tests/"])

        logger.info("Running tests: %s", component)

        subprocess.run(
            command,
            cwd=ROOT / component,
            env={**os.environ, "COVERAGE_FILE": str(ROOT / ".coverage")},
            check=True
        )


if __name__ == "__main__":
    ...