"""Tests for POST /agents/{agent_name}/runs, focused on requester attribution.

These go through a TestClient rather than calling the handler directly: the
`X-On-Behalf-Of` normalization lives in the parameter's annotation (see
`UserEmail` in app/routers/runs.py), so it only runs as part of FastAPI's request
handling. The GCS/Cloud Run collaborators are monkeypatched and the DB session is
the shared `FakeSession`, so no GCP or Postgres is needed.
"""

import pytest
from app import gcs, jobs


@pytest.fixture(autouse=True)
def _stub_gcp(monkeypatch):
    async def fake_policy(run_id):
        return {"url": "https://upload.example/", "fields": {"key": "v"}}

    async def fake_trigger(job_name, env, broker_env=None):
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
