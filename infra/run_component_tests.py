import argparse
import os
import re
import subprocess
import tomllib
from logging import INFO, basicConfig, getLogger
from pathlib import Path

basicConfig(level=INFO)
logger = getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

GLOBAL_FILES = {
    ".taskcluster.yml",
    "pyproject.toml",
    "uv.lock",
    "infra/run_component_tests.py",
}


def get_changed_files_paths(base_sha=None, base_repo=None):
    """Return changed paths, or None if the changes are unknown."""
    if not base_sha:
        return None

    try:
        if base_repo:
            subprocess.run(
                ["git", "fetch", "--no-tags", "--", base_repo, base_sha],
                cwd=ROOT,
                check=True,
            )

        merge_base = (
            subprocess.check_output(["git", "merge-base", base_sha, "HEAD"], cwd=ROOT)
            .decode()
            .strip()
        )

        paths = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                merge_base,
                "HEAD",
                "--",
            ],
            cwd=ROOT,
        )

        return {os.fsdecode(path) for path in paths.split(b"\0") if path}

    except (OSError, subprocess.CalledProcessError) as exc:
        logger.warning("Cannot determine changes; running all tests: %s", exc)
        return None


def get_components_to_test(changed_files, dependencies):
    """Select components affected by changes to their code or dependencies."""
    components = []

    for component_path in dependencies:
        if not component_path.startswith(("agents/", "services/")):
            continue

        tests_directory = ROOT / component_path / "tests"

        if any(tests_directory.rglob("test_*.py")):
            components.append(component_path)

    if changed_files is None:
        logger.info("Changes are unknown, selecting all components.")
        return components

    if changed_files & GLOBAL_FILES:
        logger.info("Shared configuration changed, selecting all components.")
        return components

    if any(Path(path).name == "pyproject.toml" for path in changed_files):
        logger.info("Project configuration changed, selecting all components.")
        return components

    selected_components = []

    for component_path in components:
        dependency_paths = get_related_paths(component_path, dependencies)
        prefixes = tuple(path + "/" for path in dependency_paths)

        for changed_file in changed_files:
            if changed_file.startswith(prefixes):
                selected_components.append(component_path)
                break

    return selected_components


def run_tests(components):
    """Run tests for the selected components and collect coverage."""
    if not components:
        logger.info("No related changes; skipping tests.")
        return

    for component_path in components:
        command = ["uv", "run", "--locked"]
        test_dependencies = [
            "pytest==9.1.0",
            "pytest-cov==7.1.0",
            "pytest-asyncio==1.4.0",
        ]

        if component_path == "services/mcp":
            test_dependencies.append("pytest-mock==3.15.1")

        for dependency in test_dependencies:
            command.extend(["--with", dependency])

        command.extend(["pytest", "--cov=.", "--cov-append", "tests/"])

        logger.info("Running tests: %s", component_path)

        subprocess.run(
            command,
            cwd=ROOT / component_path,
            env={**os.environ, "COVERAGE_FILE": str(ROOT / ".coverage")},
            check=True,
        )


def normalize_package_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def get_component_dependencies():
    """Map workspace component paths to their direct local dependencies."""
    pyproject_path = ROOT / "pyproject.toml"

    if not pyproject_path.is_file():
        raise FileNotFoundError(f"Root pyproject.toml not found: {pyproject_path}")

    with pyproject_path.open("rb") as file:
        root_config = tomllib.load(file)

    workspace = root_config["tool"]["uv"]["workspace"]
    directories = {ROOT}

    for pattern in workspace["members"]:
        directories.update(ROOT.glob(pattern))

    for pattern in workspace.get("exclude", []):
        directories.difference_update(ROOT.glob(pattern))

    package_paths = {}
    package_configs = {}

    for directory in sorted(directories):
        config_path = directory / "pyproject.toml"

        if not config_path.is_file():
            continue

        with config_path.open("rb") as file:
            pyproject_data = tomllib.load(file)

        package_name = pyproject_data["project"]["name"]

        if directory == ROOT:
            component = "bugbug"
        else:
            component = directory.relative_to(ROOT).as_posix()

        package_paths[normalize_package_name(package_name)] = component
        package_configs[component] = pyproject_data

    component_dependencies = {}

    for component, pyproject_data in package_configs.items():
        project = pyproject_data["project"]
        requirements = list(project.get("dependencies", []))

        for extra_dependencies in project.get("optional-dependencies", {}).values():
            requirements.extend(extra_dependencies)

        dependencies = set()

        for requirement in requirements:
            package_name = re.split(r"[\s\[<>=!~;@]", requirement.strip(), maxsplit=1)[
                0
            ]
            package_name = normalize_package_name(package_name)

            if package_name in package_paths:
                dependencies.add(package_paths[package_name])

        component_dependencies[component] = dependencies

    return component_dependencies


def get_related_paths(component_path, component_dependencies):
    """Return the component path and all transitive dependency paths."""
    visited_paths = set()
    paths_to_check = [component_path]

    while paths_to_check:
        current_path = paths_to_check.pop()

        if current_path in visited_paths:
            continue

        visited_paths.add(current_path)
        paths_to_check.extend(component_dependencies[current_path])

    return visited_paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run tests for components affected by changes."
    )
    parser.add_argument(
        "--base-sha", help="Base commit used to determine changed files."
    )
    parser.add_argument("--base-repo", help="Repository to fetch the base commit from.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show selected components without running tests.",
    )
    args = parser.parse_args()

    changed_files = get_changed_files_paths(args.base_sha, args.base_repo)
    dependencies = get_component_dependencies()
    components = get_components_to_test(changed_files, dependencies)

    logger.info("Components to test: %s", components)

    if not args.dry_run:
        run_tests(components)
