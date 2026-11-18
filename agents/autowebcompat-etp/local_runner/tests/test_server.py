"""Tests for the local runner's server.py, which is a PEP 723 script, not a package.

From the repo root:

    uv run --no-project --python 3.14 --with pytest --with fastapi --with httpx \
        --with python-dotenv --with pillow --with requests --with uvicorn \
        --with anthropic pytest -q agents/autowebcompat-etp/local_runner/tests
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
    monkeypatch.setattr(server, "MODELS_CACHE", None)
    return artifacts


@pytest.fixture
def client():
    # The server only answers to its own names (see LocalOnly).
    return TestClient(server.app, base_url="http://127.0.0.1:8123")


def write_run(artifacts, run_id, params, result):
    path = artifacts / run_id
    path.mkdir()
    meta = {"run_id": run_id, "params": params, "status": "ok", "started": 1.0}
    (path / server.META_FILE).write_text(json.dumps(meta))
    summary = {"status": "ok", "findings": {"result": result}}
    (path / "summary.json").write_text(json.dumps(summary))
    return path


# --- models --------------------------------------------------------------------


def test_models_fallback_when_sdk_fails(client, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(server.anthropic, "Anthropic", broken)
    body = client.get("/api/models").json()
    assert body["fallback"] is True
    assert [m["id"] for m in body["models"]] == [
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5-2",
    ]
    assert all(m["display_name"] for m in body["models"])
    assert server.MODELS_CACHE is None


def test_models_listed_newest_first_and_cached(client, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace

    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = self

        def list(self):
            calls.append(1)
            return [
                SimpleNamespace(
                    id="old", display_name="Old", created_at=datetime(2025, 1, 1)
                ),
                SimpleNamespace(
                    id="new", display_name="New", created_at=datetime(2026, 11, 1)
                ),
            ]

    monkeypatch.setattr(server.anthropic, "Anthropic", FakeClient)
    for _ in range(2):
        body = client.get("/api/models").json()
        assert body == {
            "models": [
                {"id": "new", "display_name": "New"},
                {"id": "old", "display_name": "Old"},
            ],
            "fallback": False,
        }
    assert len(calls) == 1


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


class FakePopen:
    calls = []

    def __init__(self, cmd, **kwargs):
        FakePopen.calls.append((cmd, kwargs))
        self.stdout = [b"agent output\n"]
        self.pid = 1

    def wait(self):
        return 0


@pytest.fixture
def popen(monkeypatch):
    FakePopen.calls = []
    monkeypatch.setattr(server.subprocess, "Popen", FakePopen)
    return FakePopen.calls


def execute(params):
    run = server.Run(run_id="etp-bug1-test", params=params)
    run.dir.mkdir(parents=True)
    server.execute(run)
    [(cmd, kwargs)] = FakePopen.calls
    return run, cmd, kwargs


# --- local only ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("127.0.0.1:8123", 200),
        ("localhost:8123", 200),
        ("host.docker.internal:8123", 200),
        ("evil.example:8123", 400),
        ("evil.example", 400),
    ],
)
def test_other_hosts_are_refused(client, host, status):
    assert client.get("/api/config", headers={"Host": host}).status_code == status


@pytest.mark.parametrize(
    ("origin", "status"),
    [
        (None, 200),
        ("http://127.0.0.1:8123", 200),
        # The Vite dev server proxies to the runner.
        ("http://localhost:5173", 200),
        ("https://evil.example", 403),
        ("null", 403),
    ],
)
def test_cross_origin_writes_are_refused(client, origin, status):
    headers = {"Origin": origin} if origin else {}
    response = client.post("/api/runs", json={"bug_id": 1}, headers=headers)
    assert response.status_code == status


def test_cross_origin_reads_are_allowed(client):
    # A read from another origin can't see the response (no CORS headers).
    response = client.get("/api/config", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200


# --- run limits ----------------------------------------------------------------


def test_execute_applies_the_default_max_turns(popen, monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_MAX_TURNS", 70)
    _, cmd, _ = execute({"bug_id": 1})
    assert "MAX_TURNS=70" in cmd


def test_execute_keeps_the_runs_max_turns(popen, monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_MAX_TURNS", 70)
    _, cmd, _ = execute({"bug_id": 1, "max_turns": 12})
    assert "MAX_TURNS=12" in cmd
    assert "MAX_TURNS=70" not in cmd


def test_execute_without_a_default_max_turns(popen, monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_MAX_TURNS", 0)
    _, cmd, _ = execute({"bug_id": 1})
    assert not [arg for arg in cmd if arg.startswith("MAX_TURNS=")]


def test_time_out_stops_the_run(monkeypatch):
    stopped = []
    monkeypatch.setattr(server.subprocess, "run", lambda cmd, **kw: stopped.append(cmd))
    run = server.Run(run_id="etp-bug1-test", params={"bug_id": 1})
    run.dir.mkdir(parents=True)
    server.time_out(run, 30)
    assert run.timed_out
    assert stopped == [["docker", "stop", run.container]]
    assert "timed out after 30 min" in run.lines[-1]


def test_time_out_leaves_a_finished_run_alone(monkeypatch):
    stopped = []
    monkeypatch.setattr(server.subprocess, "run", lambda cmd, **kw: stopped.append(cmd))
    run = server.Run(run_id="etp-bug1-test", params={"bug_id": 1}, finished=1.0)
    run.dir.mkdir(parents=True)
    server.time_out(run, 30)
    assert not run.timed_out and not stopped


def test_a_timed_out_run_ends_as_an_error(popen):
    class TimingOut(FakePopen):
        def wait(self):
            run.timed_out = True
            return 143

    server.subprocess.Popen = TimingOut
    run = server.Run(run_id="etp-bug1-test", params={"bug_id": 1})
    run.dir.mkdir(parents=True)
    server.execute(run)
    assert run.status == server.RunStatus.ERROR


def test_config_reports_the_run_limits(client, monkeypatch):
    monkeypatch.setattr(server, "DEFAULT_MAX_TURNS", 70)
    monkeypatch.setattr(server, "RUN_TIMEOUT_MINUTES", 0)
    config = client.get("/api/config").json()
    assert config["default_max_turns"] == 70
    assert config["run_timeout_minutes"] is None
