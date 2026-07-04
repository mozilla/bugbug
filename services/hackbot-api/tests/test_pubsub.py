"""Tests for event publishing: the attributes consumers filter on.

Subscription filters match only on message attributes (never the body), so
these lock in the attribute set a run.completed event carries — the routing
keys the action-applier's filter (and future consumers') depend on.
"""

import json

from app import pubsub


def test_build_event_merges_event_type_and_schema_version():
    data, attrs = pubsub._build_event(
        "run.completed",
        {"run_id": "r1", "agent": "bug-fix", "status": "succeeded"},
        {"agent": "bug-fix", "status": "succeeded"},
    )
    assert attrs == {
        "event_type": "run.completed",
        "schema_version": pubsub.EVENT_SCHEMA_VERSION,
        "agent": "bug-fix",
        "status": "succeeded",
    }
    assert json.loads(data) == {
        "run_id": "r1",
        "agent": "bug-fix",
        "status": "succeeded",
    }


async def test_publish_run_completed_publishes_filterable_attributes(monkeypatch):
    captured = {}

    def fake_sync(topic, data, attributes):
        captured["topic"] = topic
        captured["data"] = data
        captured["attributes"] = attributes
        return "msg-id"

    monkeypatch.setattr(pubsub, "_publish_sync", fake_sync)

    await pubsub.publish_run_completed("run-1", "bug-fix", "failed")

    assert captured["topic"] == pubsub.settings.run_events_topic
    # The keys the applier subscription filter matches on must be present.
    assert captured["attributes"]["event_type"] == "run.completed"
    assert captured["attributes"]["status"] == "failed"
    assert captured["attributes"]["agent"] == "bug-fix"
    assert json.loads(captured["data"])["run_id"] == "run-1"


async def test_publish_failure_is_swallowed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("pubsub down")

    monkeypatch.setattr(pubsub, "_publish_sync", boom)
    # Best-effort: a publish failure must not propagate (run is already
    # finalized before this is called).
    await pubsub.publish_run_completed("run-1", "bug-fix", "succeeded")
