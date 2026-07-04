"""Tests for the Pub/Sub push envelope decoding used by both internal event routes (agent-run-finished, apply-run-actions)."""

import base64
import json

from app.routers.events import _cloud_run_execution_name, _decode_pubsub_push_body


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


def test_cloud_run_execution_name_from_proto_payload():
    event = {
        "protoPayload": {
            "resourceName": "projects/p/locations/l/jobs/j/executions/e",
            "methodName": "google.cloud.run.v2.Executions.RunExecution",
        }
    }
    assert (
        _cloud_run_execution_name(event) == "projects/p/locations/l/jobs/j/executions/e"
    )


def test_cloud_run_execution_name_missing():
    assert _cloud_run_execution_name({"protoPayload": {}}) is None
    assert _cloud_run_execution_name({}) is None
