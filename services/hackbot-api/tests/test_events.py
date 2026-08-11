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

import pytest
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


# --- apply-run-actions: undecodable input is acked, not retried ----------- #


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


def _patch_route(monkeypatch):
    """Stub the applier; record that it was called."""
    calls = {"order": []}

    async def fake_on_run_completed(db, run):
        calls["order"].append("apply")

    monkeypatch.setattr(events, "on_run_completed", fake_on_run_completed)
    return calls


@pytest.mark.parametrize(
    "body",
    [
        {},  # no envelope
        {"message": {"data": "bm90LWpzb24="}},  # decodes to "not-json"
        {"message": {"data": base64.b64encode(b'{"run_id": "nope"}').decode()}},
    ],
    ids=["no-envelope", "not-json", "bad-uuid"],
)
async def test_an_undecodable_message_is_acked_not_retried(monkeypatch, body):
    # A message that can never become valid must not 5xx: Pub/Sub would nack, and
    # with no dead-letter topic it would come back for the whole retention window.
    _patch_route(monkeypatch)
    request = SimpleNamespace(json=AsyncMock(return_value=body))
    await events.apply_run_actions(request, db=_FakeDB(_FakeRun()))
