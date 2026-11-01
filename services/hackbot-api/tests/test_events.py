"""Tests for the internal event routes (agent-run-finished, apply-run-actions).

Covers the Pub/Sub push envelope decode and extracting the execution name from
a Cloud Run Jobs `system_event` completion LogEntry (routed via a logging sink).
"""

import base64
import json

from app.routers.events import (
    _decode_pubsub_push_body,
    _execution_name_from_completion_log,
)


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
