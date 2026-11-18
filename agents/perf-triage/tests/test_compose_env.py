"""Every AgentInputs field must be passed through by compose, or Docker drops it."""

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


def test_calculator_and_rankings_are_passed_through():
    env = _env()
    assert "PERF_TRIAGE_PATH" in env
    assert "PERF_TRIAGE_TOP_SITES" not in env
    assert env["PERF_PLUGIN_DIR"] == "/plugins/perf"


def test_every_module_the_dockerfile_runs_exists():
    # Left out of the first split from perf-repro-agent: every run then failed
    # closed as "bug lookup failed", which no other test noticed.
    import importlib
    import re
    from pathlib import Path

    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    modules = re.findall(r'CMD \["python", "-m", "([\w.]+)"\]', dockerfile)
    assert "hackbot_agents.perf_triage.broker" in modules
    for module in modules:
        assert importlib.util.find_spec(module) is not None, module
