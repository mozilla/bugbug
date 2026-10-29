import pytest

from infra import run_component_tests as runner


@pytest.fixture
def dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    components = ["agents/bug-fix", "agents/build-repair", "services/hackbot-api"]

    for component in components:
        tests_directory = tmp_path / component / "tests"
        tests_directory.mkdir(parents=True)
        (tests_directory / "test_example.py").touch()

    return {
        "agents/bug-fix": {"libs/hackbot-runtime"},
        "agents/build-repair": {"libs/agent-tools"},
        "services/hackbot-api": set(),
        "libs/hackbot-runtime": {"libs/agent-tools"},
        "libs/agent-tools": set(),
    }


@pytest.mark.parametrize(
    "changed_files, expected",
    [
        ({"agents/bug-fix/main.py"}, {"agents/bug-fix"}),
        ({"libs/agent-tools/tool.py"}, {"agents/bug-fix", "agents/build-repair"}),
        ({"services/hackbot-api/app/main.py"}, {"services/hackbot-api"}),
        (
            {"uv.lock"},
            {"agents/bug-fix", "agents/build-repair", "services/hackbot-api"},
        ),
        (None, {"agents/bug-fix", "agents/build-repair", "services/hackbot-api"}),
        (set(), set()),
        ({"README.md"}, set()),
        ({"agents/bug-fix-extra/main.py"}, set()),
    ],
)
def test_components_to_test(dependencies, changed_files, expected):
    selected = runner.get_components_to_test(changed_files, dependencies)

    assert set(selected) == expected
    assert len(selected) == len(set(selected))


def test_dependency_cycle():
    dependencies = {"libs/first": {"libs/second"}, "libs/second": {"libs/first"}}

    related_paths = runner.get_related_paths("libs/first", dependencies)

    assert related_paths == {"libs/first", "libs/second"}


def test_component_without_tests_is_skipped(dependencies, tmp_path):
    tests_directory = tmp_path / "services/empty/tests"
    tests_directory.mkdir(parents=True)
    (tests_directory / "__init__.py").touch()

    dependencies["services/empty"] = set()

    selected = runner.get_components_to_test(None, dependencies)

    assert "services/empty" not in selected


def test_dependencies_from_pyproject(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)

    pyprojects = {
        "": """
    [project]
    name = "bugbug"

    [tool.uv.workspace]
    members = ["agents/*", "libs/*"]
    """,
        "agents/bug-fix": """
    [project]
    name = "bug-fix"
    dependencies = ["hackbot_runtime[extra]>=1.0", "pytest>=8"]
    """,
        "libs/hackbot-runtime": """
    [project]
    name = "hackbot-runtime"
    dependencies = ["agent-tools"]

    [project.optional-dependencies]
    extra = ["httpx"]
    """,
        "libs/agent-tools": """
    [project]
    name = "agent-tools"
    dependencies = []
    """,
    }

    for directory, content in pyprojects.items():
        project_directory = tmp_path / directory
        project_directory.mkdir(parents=True, exist_ok=True)
        (project_directory / "pyproject.toml").write_text(
            content,
            encoding="utf-8",
        )

    dependencies = runner.get_component_dependencies()

    assert dependencies == {
        "bugbug": set(),
        "agents/bug-fix": {"libs/hackbot-runtime"},
        "libs/hackbot-runtime": {"libs/agent-tools"},
        "libs/agent-tools": set(),
    }
