"""Tests for starting a chained run from an `agents.start_run` action."""

import uuid

from app.action_handlers import agents_handler
from app.action_handlers.base import ApplyContext
from hackbot_client import TriggeredRun


async def _no_download(key, max_bytes=None):
    raise AssertionError("not expected")


def _fake_trigger(calls, child, status="pending"):
    async def fake_trigger(self, agent_name, inputs, *, dedupe_key=None):
        calls.append((agent_name, inputs, dedupe_key))
        return TriggeredRun(run_id=child, agent=agent_name, status=status, is_new=True)

    return fake_trigger


async def test_the_chained_run_is_keyed_by_its_parent(monkeypatch):
    calls = []
    child = uuid.uuid4()
    fake_trigger = _fake_trigger(calls, child)

    monkeypatch.setattr(agents_handler.HackbotClient, "trigger_run", fake_trigger)
    ctx = ApplyContext(
        run_id="p-1", agent="first-agent", download_artifact=_no_download
    )

    result = await agents_handler.StartRunHandler().apply({"agent": "next-agent"}, ctx)

    assert calls == [("next-agent", {"parent_run_id": "p-1"}, "parent-p-1")]
    assert result.status == "applied"
    assert result.result["run_id"] == str(child)
    assert result.result["url"].endswith(f"/runs/{child}")


async def test_a_child_that_failed_to_start_fails_the_action(monkeypatch):
    monkeypatch.setattr(
        agents_handler.HackbotClient,
        "trigger_run",
        _fake_trigger([], uuid.uuid4(), status="failed"),
    )
    ctx = ApplyContext(
        run_id="p-1", agent="first-agent", download_artifact=_no_download
    )

    result = await agents_handler.StartRunHandler().apply({"agent": "next-agent"}, ctx)

    assert result.status == "failed"
    assert "failed to start" in result.error
