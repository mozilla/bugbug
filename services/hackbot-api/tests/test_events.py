"""Tests for the internal event routes (agent-run-finished, apply-run-actions).

Covers the Pub/Sub push envelope decode and extracting the execution name from
a Cloud Run Jobs `system_event` completion LogEntry (routed via a logging sink).
"""

import base64
import json
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.routers import events
from app.routers.events import (
    _decode_pubsub_push_body,
    _execution_name_from_completion_log,
)
from app.schemas import RunStatus


def _push_envelope(payload: dict) -> dict:
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    return {"message": {"data": data, "messageId": "1"}, "subscription": "sub"}


def test_decode_pubsub_push_body_round_trips():
    body = _push_envelope({"run_id": "abc", "status": "succeeded"})
    assert _decode_pubsub_push_body(body) == {"run_id": "abc", "status": "succeeded"}


def test_decode_pubsub_push_body_missing_message():
    assert _decode_pubsub_push_body({}) == {}


def test_decode_pubsub_push_body_missing_data():
    assert _decode_pubsub_push_body({"message": {}}) == {}


def _completion_log(status: str) -> dict:
    """A Cloud Run Jobs execution-completion system_event LogEntry."""
    return {
        "protoPayload": {
            "resourceName": "projects/p/locations/l/jobs/j/executions/e",
            "response": {
                "status": {"conditions": [{"type": "Completed", "status": status}]}
            },
        },
        "resource": {
            "type": "cloud_run_job",
            "labels": {"job_name": "hackbot-agent-bug-fix"},
        },
    }


def test_execution_name_from_completion_log_success_and_failure():
    # Both terminal outcomes carry the same execution resourceName.
    for status in ("True", "False"):
        assert (
            _execution_name_from_completion_log(_completion_log(status))
            == "projects/p/locations/l/jobs/j/executions/e"
        )


def test_execution_name_falls_back_to_response_metadata_name():
    entry = {
        "protoPayload": {
            "response": {"metadata": {"name": "namespaces/p/executions/e"}}
        }
    }
    assert _execution_name_from_completion_log(entry) == "namespaces/p/executions/e"


def test_execution_name_falls_back_to_labels():
    entry = {"labels": {"run.googleapis.com/execution_name": "e-123"}}
    assert _execution_name_from_completion_log(entry) == "e-123"


def test_execution_name_missing():
    assert _execution_name_from_completion_log({"protoPayload": {}}) is None
    assert _execution_name_from_completion_log({}) is None


# --- apply-run-actions: applying, then telling the team ------------------- #
#
# Notifying has to come after the apply, because the message asserts whether Bugzilla
# was written — which is why it isn't a second consumer of the same event.


@dataclass
class _FakeRun:
    run_id: uuid.UUID = field(default_factory=lambda: uuid.UUID(int=7))
    agent: str = "frontend-triage"
    status: str = RunStatus.succeeded.value
    summary: dict | None = None
    inputs: dict = field(default_factory=dict)


class _FakeDB:
    def __init__(self, run):
        self._run = run

    async def get(self, model, run_id):
        return self._run

    async def commit(self):
        pass


def _patch_route(monkeypatch, *, notify=True):
    """Stub the applier and the notifier; record the order they were called in."""
    calls = {"order": []}

    async def fake_on_run_completed(db, run):
        calls["order"].append("apply")

    async def fake_notify(db, run):
        calls["order"].append("notify")

    monkeypatch.setattr(events, "on_run_completed", fake_on_run_completed)
    monkeypatch.setattr(events, "notify_run_completed", fake_notify)
    monkeypatch.setattr(
        events,
        "AGENT_REGISTRY",
        {"frontend-triage": SimpleNamespace(notify_completion=notify)},
    )
    return calls


async def _post(monkeypatch, run, **kwargs):
    calls = _patch_route(monkeypatch, **kwargs)
    request = SimpleNamespace(
        json=AsyncMock(return_value=_push_envelope({"run_id": str(run.run_id)}))
    )
    await events.apply_run_actions(request, db=_FakeDB(run))
    return calls


async def test_notifies_after_applying(monkeypatch):
    # The message reports whether Bugzilla was written, so it cannot go out first.
    calls = await _post(monkeypatch, _FakeRun())
    assert calls["order"] == ["apply", "notify"]


async def test_does_not_notify_for_agents_that_did_not_opt_in(monkeypatch):
    calls = await _post(monkeypatch, _FakeRun(), notify=False)
    assert calls["order"] == ["apply"]



async def test_a_notification_failure_does_not_fail_the_route(monkeypatch):
    # By this point the Bugzilla writes have landed. A 500 here would earn a
    # Pub/Sub redelivery of work that is already done.
    run = _FakeRun()
    _patch_route(monkeypatch)

    async def boom(db, run):
        raise RuntimeError("sendgrid exploded")

    monkeypatch.setattr(events, "notify_run_completed", boom)
    request = SimpleNamespace(
        json=AsyncMock(return_value=_push_envelope({"run_id": str(run.run_id)}))
    )

    await events.apply_run_actions(request, db=_FakeDB(run))
