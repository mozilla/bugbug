"""Tests for POST /agents/{agent_name}/runs, focused on author attribution.

Follows this suite's fake-based style: the handler is called directly with a fake
session that captures the Run it adds, and the GCS/Cloud Run collaborators are
monkeypatched, so no GCP or Postgres is needed.
"""

import pytest
from app import gcs, jobs
from app.routers import runs as runs_router


class _CapturingDB:
    """Captures the ORM object passed to add(); commit/flush are no-ops."""

    def __init__(self):
        self.added = None

    def add(self, obj):
        self.added = obj

    async def flush(self):
        pass

    async def commit(self):
        pass


@pytest.fixture(autouse=True)
def _stub_gcp(monkeypatch):
    async def fake_policy(run_id):
        return {"url": "https://upload.example/", "fields": {"key": "v"}}

    async def fake_trigger(job_name, env):
        return "projects/p/locations/l/jobs/j/executions/e"

    monkeypatch.setattr(gcs, "run_prefix", lambda run_id: f"results/{run_id}/")
    monkeypatch.setattr(gcs, "generate_results_policy", fake_policy)
    monkeypatch.setattr(jobs, "trigger_execution", fake_trigger)


async def _create(author, db=None):
    db = db or _CapturingDB()
    await runs_router.create_run(
        agent_name="bug-fix",
        payload={"bug_id": 1889001},
        author=author,
        db=db,
    )
    return db.added


async def test_create_run_records_author_from_header():
    run = await _create("someone@mozilla.com")
    assert run.author == "someone@mozilla.com"


async def test_create_run_normalizes_author_case_and_whitespace():
    # Stored lowercased/stripped so the list_runs filter can match exactly.
    run = await _create("  Someone@Mozilla.COM \n")
    assert run.author == "someone@mozilla.com"


@pytest.mark.parametrize("header", [None, "", "   "])
async def test_create_run_leaves_run_unattributed_without_author(header):
    # Automation (e.g. the Phabricator webhook) omits the header entirely; a
    # blank one must not land as an empty-string author either.
    run = await _create(header)
    assert run.author is None
