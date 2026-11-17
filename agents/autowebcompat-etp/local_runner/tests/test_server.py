"""Tests for the local runner's server.py, which is a PEP 723 script, not a package.

From the repo root:

    uv run --no-project --python 3.14 --with pytest --with fastapi --with httpx \
        --with python-dotenv --with pillow --with requests --with uvicorn \
        pytest -q agents/autowebcompat-etp/local_runner/tests
"""

import importlib.util
import json
import os
import queue
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVER_PY = Path(__file__).resolve().parents[1] / "server.py"

# Keep the import from touching ~/hackbot; each test points the globals at its
# own tmp_path.
os.environ["HACKBOT_ARTIFACTS"] = str(Path(tempfile.mkdtemp()) / "artifacts")
spec = importlib.util.spec_from_file_location("etp_local_runner_server", SERVER_PY)
server = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = server
spec.loader.exec_module(server)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(server, "ARTIFACTS_ROOT", artifacts)
    monkeypatch.setattr(server, "RUNS", {})
    # The worker threads wait on the original queue, so nothing queued here runs.
    monkeypatch.setattr(server, "RUN_QUEUE", queue.Queue())
    return artifacts


@pytest.fixture
def client():
    return TestClient(server.app)


def write_run(artifacts, run_id, params, result):
    path = artifacts / run_id
    path.mkdir()
    meta = {"run_id": run_id, "params": params, "status": "ok", "started": 1.0}
    (path / server.META_FILE).write_text(json.dumps(meta))
    summary = {"status": "ok", "findings": {"result": result}}
    (path / "summary.json").write_text(json.dumps(summary))
    return path


# --- Bugzilla actions ----------------------------------------------------------


@pytest.fixture
def bugzilla_calls(monkeypatch):
    calls = []

    def fake(method, path, body=None, params=None, auth_required=True):
        calls.append((method, path, body, params))
        if method == "GET":
            return {
                "bugs": [
                    {
                        "whiteboard": "[old]",
                        "cf_user_story": "",
                        "severity": "--",
                        "priority": "P3",
                        "creator": "reporter@example.com",
                    }
                ]
            }
        return {}

    monkeypatch.setattr(server, "bugzilla", fake)
    return calls


def test_apply_closes_and_needinfos(client, isolated, bugzilla_calls):
    write_run(isolated, "etp-bug7-20261117-100000", {"bug_id": 7}, {})
    response = client.post(
        "/api/runs/etp-bug7-20261117-100000/apply",
        json={
            "bug_id": 7,
            "comment": "Works now.",
            "changes": {"severity": "S3"},
            "resolution": "WORKSFORME",
            "needinfo": "reporter@example.com",
        },
    )
    assert response.status_code == 200
    assert bugzilla_calls == [
        (
            "PUT",
            "bug/7",
            {
                "severity": "S3",
                "status": "RESOLVED",
                "resolution": "WORKSFORME",
                "flags": [
                    {
                        "name": "needinfo",
                        "status": "?",
                        "requestee": "reporter@example.com",
                    }
                ],
                "comment": {"body": "Works now.", "is_markdown": True},
            },
            None,
        )
    ]


def test_apply_without_close_or_needinfo(client, isolated, bugzilla_calls):
    write_run(isolated, "etp-bug7-20261117-100000", {"bug_id": 7}, {})
    response = client.post(
        "/api/runs/etp-bug7-20261117-100000/apply",
        json={"bug_id": 7, "changes": {"priority": "P2"}},
    )
    assert response.status_code == 200
    assert bugzilla_calls == [("PUT", "bug/7", {"priority": "P2"}, None)]


def test_apply_rejects_unknown_resolution(client, isolated, bugzilla_calls):
    write_run(isolated, "etp-bug7-20261117-100000", {"bug_id": 7}, {})
    response = client.post(
        "/api/runs/etp-bug7-20261117-100000/apply",
        json={"bug_id": 7, "resolution": "DUPLICATE"},
    )
    assert response.status_code == 422
    assert bugzilla_calls == []


@pytest.mark.parametrize(
    ("failure_reason", "resolution", "close", "needinfo"),
    [
        ("not_reproducible", "WORKSFORME", True, True),
        ("broken_in_standard", "INVALID", True, False),
        ("anti_adblock", "WONTFIX", False, False),
        (None, None, False, False),
    ],
)
def test_review_suggests_outcome_actions(
    client, isolated, failure_reason, resolution, close, needinfo
):
    write_run(
        isolated,
        "etp-bug7-20261117-100000",
        {"bug_id": 7},
        {"failure_reason": failure_reason},
    )
    draft = client.get("/api/runs/etp-bug7-20261117-100000/review").json()["draft"]
    assert (draft["resolution"], draft["close"], draft["needinfo"]) == (
        resolution,
        close,
        needinfo,
    )


def test_bug_draft_loads_reporter_and_changes(client, isolated, bugzilla_calls):
    write_run(
        isolated,
        "etp-bug7-20261117-100000",
        {"bug_id": 7},
        {"whiteboard_tags": ["[etp-tag]"], "severity": "S3", "priority": "P1"},
    )
    response = client.get("/api/runs/etp-bug7-20261117-100000/bug-draft")
    assert response.status_code == 200
    assert response.json() == {
        "changes": {"severity": "S3", "whiteboard": "[old][etp-tag]"},
        "reporter": "reporter@example.com",
    }
    assert [call[:2] for call in bugzilla_calls] == [("GET", "bug/7")]
