"""Every AgentInputs field must be passed through by compose, or Docker drops it."""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = Path(__file__).resolve().parents[1] / "compose.yml"


def _env() -> dict[str, str | None]:
    spec = yaml.safe_load(COMPOSE.read_text())
    env = spec["services"]["perf-triage-agent"]["environment"]
    return {
        item.split("=", 1)[0].strip(): (item.split("=", 1)[1] if "=" in item else None)
        for item in env
    }


def test_every_agent_input_is_passed_through():
    from hackbot_agents.perf_triage.__main__ import AgentInputs

    missing = {n.upper() for n in AgentInputs.model_fields} - set(_env())
    assert not missing


def test_calculator_path_defaults_to_the_image_copy():
    assert _env()["PERF_TRIAGE_PATH"] == "${PERF_TRIAGE_PATH:-/app/perf_triage.py}"


def test_every_module_the_dockerfile_runs_exists():
    # Left out of the first split from perf-repro-agent: every run then failed
    # closed as "bug lookup failed", which no other test noticed.
    import importlib
    import re
    from pathlib import Path

    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    modules = re.findall(r'CMD \["python", "-m", "([\w.]+)"\]', dockerfile)
    assert "performance_tools.broker" in modules
    for module in modules:
        assert importlib.util.find_spec(module) is not None, module
