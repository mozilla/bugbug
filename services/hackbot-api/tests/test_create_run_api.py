"""Tests for POST /agents/{agent_name}/runs: requester attribution and dedupe.

These go through a TestClient rather than calling the handler directly: the
`X-On-Behalf-Of` normalization and the dedupe key live in the parameters'
annotations (`UserEmail` and `DedupeKey` in app/routers/runs.py), so they only
run as part of FastAPI's request handling. The
GCS/Cloud Run collaborators are monkeypatched and the DB session is the shared
`FakeSession`, so no GCP or Postgres is needed.
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from app import gcs, jobs
from app.schemas import RunStatus


@pytest.fixture(autouse=True)
def _stub_gcp(monkeypatch):
    async def fake_policy(run_id):
        return {"url": "https://upload.example/", "fields": {"key": "v"}}

    async def fake_trigger(job_name, env, broker_env):
        return "projects/p/locations/l/jobs/j/executions/e"

    monkeypatch.setattr(gcs, "run_prefix", lambda run_id: f"results/{run_id}/")
    monkeypatch.setattr(gcs, "generate_results_policy", fake_policy)
    monkeypatch.setattr(jobs, "trigger_execution", fake_trigger)


def _create(client, headers=None):
    resp = client.post(
        "/agents/bug-fix/runs", json={"bug_id": 1889001}, headers=headers or {}
    )
    assert resp.status_code == 201, resp.text
    return resp


def test_create_run_records_requested_by_from_header(client, db):
    _create(client, {"X-On-Behalf-Of": "someone@mozilla.com"})
    assert db.added.requested_by == "someone@mozilla.com"


def test_create_run_normalizes_requested_by_case_and_whitespace(client, db):
    # Stored lowercased/stripped so the list_runs filter can match exactly.
    _create(client, {"X-On-Behalf-Of": "  Someone@Mozilla.COM  "})
    assert db.added.requested_by == "someone@mozilla.com"


@pytest.mark.parametrize("headers", [{}, {"X-On-Behalf-Of": "   "}])
def test_create_run_leaves_run_unattributed_without_header(client, db, headers):
    # Automation (e.g. the Phabricator webhook) omits the header entirely; a
    # blank one must not land as an empty-string requester either.
    _create(client, headers)
    assert db.added.requested_by is None


# --- deduplication ---


def _holding_run():
    """A run already holding the key, as the dedupe lookup would return it."""
    return SimpleNamespace(
        run_id=uuid.uuid4(),
        agent="bug-fix",
        status=RunStatus.running.value,
        dedupe_key="push:autoland:abc",
    )


def _create_keyed(client, key="push:autoland:abc"):
    return client.post(
        "/agents/bug-fix/runs",
        json={"bug_id": 1889001},
        params={"dedupe_key": key},
    )


def _lose_the_key(db, winner):
    """Make the next insert lose the key to `winner`, as the index would.

    ON CONFLICT DO NOTHING writes nothing and returns nothing, and the run that
    won the key is there to be read afterwards, which is the only way a request
    collapses.
    """
    db.conflict = True
    db.matches = [winner]


def test_keyed_request_records_the_key_on_the_new_run(client, db):
    # Stripped, so the same name spelled with stray whitespace is the same name.
    assert _create_keyed(client, key="  push:autoland:abc  ").status_code == 201
    assert db.added.dedupe_key == "push:autoland:abc"


def test_keyed_request_is_answered_with_the_run_holding_the_key(client, db):
    winner = _holding_run()
    _lose_the_key(db, winner)

    resp = _create_keyed(client)

    # 200, not 201: this request created nothing.
    assert resp.status_code == 200, resp.text
    assert resp.json()["run_id"] == str(winner.run_id)
    # Nothing of ours was written, and skipping the conflict rather than raising
    # it leaves the transaction usable, so there is nothing to roll back either.
    assert db.commits == 0
    assert db.rollbacks == 0
    # The run handed back is looked up by agent as well as by key: two agents
    # may hold the same key, and the other one's run is a different answer.
    lookup = str(db.stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "runs.agent = 'bug-fix'" in lookup
    assert "runs.dedupe_key = 'push:autoland:abc'" in lookup


@pytest.mark.parametrize(
    ("module", "attr"),
    [(gcs, "generate_results_policy"), (jobs, "trigger_execution")],
)
def test_a_run_that_cannot_start_records_why_and_keeps_its_key(
    client, db, monkeypatch, module, attr
):
    # Both happen after the claim, so both are one failure to a caller: the run
    # exists, says why, and holds its name still, so a repeated trigger gets the
    # failure rather than quietly starting the work again.
    async def fail(*_a, **_k):
        raise RuntimeError("no quota")

    monkeypatch.setattr(module, attr, fail)

    assert _create_keyed(client).status_code == 502
    assert db.added.status == RunStatus.failed.value
    assert "no quota" in db.added.error
    assert db.added.dedupe_key == "push:autoland:abc"


@pytest.mark.parametrize("raw", ["", "   ", "x" * 500])
def test_an_unusable_dedupe_key_is_rejected(client, db, raw):
    # Blank is not taken as "no key": a caller that built a name out of a value
    # it did not have is told, rather than every such caller sharing the empty
    # name.
    assert _create_keyed(client, key=raw).status_code == 422
    assert db.added is None


# --- chained runs ---


def _parent(**fields):
    defaults = {
        "agent": "test-repair",
        "status": RunStatus.succeeded.value,
        "artifacts": [],
    }
    return SimpleNamespace(run_id=uuid.uuid4(), **{**defaults, **fields})


def _chain(client, db, monkeypatch, parent):
    async def get(model, key):
        return parent if parent and key == parent.run_id else None

    async def fake_urls(run_id, names, expiration_seconds):
        return {name: f"https://get.example/{run_id}/{name}" for name in names}

    envs = []

    async def fake_trigger(job_name, env):
        envs.append(env)
        return "projects/p/locations/l/jobs/j/executions/e"

    monkeypatch.setattr(db, "get", get)
    monkeypatch.setattr(gcs, "generate_artifact_download_urls", fake_urls)
    monkeypatch.setattr(jobs, "trigger_execution", fake_trigger)
    parent_run_id = parent.run_id if parent else uuid.uuid4()
    resp = client.post(
        "/agents/test-repair-fix/runs", json={"parent_run_id": str(parent_run_id)}
    )
    return resp, envs


def test_a_chained_run_gets_signed_urls_for_its_parents_artifacts(
    client, db, monkeypatch
):
    parent = _parent(artifacts=[{"name": "verdict.json"}])

    resp, envs = _chain(client, db, monkeypatch, parent)

    assert resp.status_code == 201, resp.text
    assert envs[0]["PARENT_RUN_ID"] == str(parent.run_id)
    assert json.loads(envs[0]["PARENT_ARTIFACTS"]) == {
        "verdict.json": f"https://get.example/{parent.run_id}/verdict.json"
    }


def test_a_chained_run_needs_a_succeeded_parent(client, db, monkeypatch):
    resp, envs = _chain(
        client, db, monkeypatch, _parent(status=RunStatus.running.value)
    )

    assert resp.status_code == 422
    assert "has not succeeded" in resp.text
    assert envs == []


def test_a_chained_run_needs_an_existing_parent(client, db, monkeypatch):
    resp, envs = _chain(client, db, monkeypatch, None)

    assert resp.status_code == 422
    assert "Unknown parent run" in resp.text
    assert db.added is None
    assert envs == []


def test_an_unchained_run_gets_no_parent_artifacts(client, db, monkeypatch):
    envs = []

    async def fake_trigger(job_name, env):
        envs.append(env)
        return "projects/p/locations/l/jobs/j/executions/e"

    monkeypatch.setattr(jobs, "trigger_execution", fake_trigger)
    _create(client)
    assert "PARENT_ARTIFACTS" not in envs[0]
