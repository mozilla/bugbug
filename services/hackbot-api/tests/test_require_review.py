"""Tests for the shared `require_review` run input."""

import pytest
from app import gcs, jobs
from app.agents import AGENT_REGISTRY, model_to_env
from app.routers.runs import create_run
from app.schemas import AgentInputs


class _FakeDB:
    def __init__(self):
        self.added = None
        self.commits = 0

    def add(self, value):
        self.added = value

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1


async def _create(monkeypatch, payload: dict, agent: str = "bug-fix"):
    triggered = {}

    async def fake_policy(run_id):
        return {"url": "https://upload.example/", "fields": {"key": "v"}}

    async def fake_trigger(job_name, env):
        triggered["job_name"] = job_name
        triggered["env"] = env
        return "projects/p/locations/l/jobs/j/executions/e"

    monkeypatch.setattr(gcs, "run_prefix", lambda run_id: f"results/{run_id}/")
    monkeypatch.setattr(gcs, "generate_results_policy", fake_policy)
    monkeypatch.setattr(jobs, "trigger_execution", fake_trigger)

    db = _FakeDB()
    await create_run(agent, payload, on_behalf_of=None, db=db)
    return db.added, triggered


# --- what reaches the database ------------------------------------------- #


async def test_explicit_review_is_persisted(monkeypatch):
    run, _ = await _create(monkeypatch, {"bug_id": 1889001, "require_review": True})
    assert run.inputs["require_review"] is True


async def test_explicit_false_is_persisted(monkeypatch):
    run, _ = await _create(monkeypatch, {"bug_id": 1889001, "require_review": False})
    assert run.inputs["require_review"] is False


async def test_omitted_flag_defaults_to_false(monkeypatch):
    run, _ = await _create(monkeypatch, {"bug_id": 1889001})
    assert run.inputs["require_review"] is False


async def test_flag_is_stored_alongside_the_agents_own_inputs(monkeypatch):
    run, _ = await _create(monkeypatch, {"bug_id": 1889001, "require_review": True})
    assert run.inputs["bug_id"] == 1889001
    assert run.inputs["require_review"] is True


async def test_every_agent_accepts_and_persists_the_flag(monkeypatch):
    for agent, payload in (
        ("bug-fix", {"bug_id": 1}),
        ("autowebcompat-repro", {"bug_id": 1}),
        ("build-repair", {"failure_tasks": {"t": "1"}}),
    ):
        for value in (True, False):
            run, _ = await _create(
                monkeypatch, {**payload, "require_review": value}, agent=agent
            )
            assert run.inputs["require_review"] is value


# --- what the agent is allowed to see ------------------------------------ #


async def test_flag_never_reaches_the_container_env(monkeypatch):
    _, triggered = await _create(
        monkeypatch, {"bug_id": 1889001, "require_review": True}
    )
    assert "REQUIRE_REVIEW" not in triggered["env"]
    assert triggered["env"]["BUG_ID"] == "1889001"


def test_model_to_env_withholds_every_platform_field():
    for spec in AGENT_REGISTRY.values():
        env = model_to_env(spec.input_schema.model_construct(require_review=True))
        for field in AgentInputs.model_fields:
            assert field.upper() not in env


# --- validation ---------------------------------------------------------- #


async def test_non_boolean_flag_is_rejected(monkeypatch):
    with pytest.raises(Exception) as exc:
        await _create(monkeypatch, {"bug_id": 1889001, "require_review": "sometimes"})
    assert getattr(exc.value, "status_code", None) == 422


def test_flag_is_declared_once_on_the_shared_base():
    assert "require_review" in AgentInputs.model_fields
    for spec in AGENT_REGISTRY.values():
        assert issubclass(spec.input_schema, AgentInputs)
        assert "require_review" in spec.input_schema.model_fields
