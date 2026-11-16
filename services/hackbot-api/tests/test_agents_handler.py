"""Tests for starting a chained run from an `agents.start_run` action."""

import uuid

from app.action_handlers import agents_handler
from app.action_handlers.base import ApplyContext
from hackbot_client import TriggeredRun


async def _no_download(key, max_bytes=None):
    raise AssertionError("not expected")


class _FakeHackbotClient:
    def __init__(self, calls, child, status="pending"):
        self.calls = calls
        self.child = child
        self.status = status

    async def trigger_run(self, agent_name, inputs, *, dedupe_key=None):
        self.calls.append((agent_name, inputs, dedupe_key))
        return TriggeredRun(
            run_id=self.child, agent=agent_name, status=self.status, is_new=True
        )


async def test_the_chained_run_is_keyed_by_its_parent():
    calls = []
    child = uuid.uuid4()
    ctx = ApplyContext(
        run_id="p-1",
        agent="first-agent",
        download_artifact=_no_download,
        hackbot_client=_FakeHackbotClient(calls, child),
    )

    result = await agents_handler.StartRunHandler().apply(
        {"agent": "next-agent", "artifacts": ["a.md"]}, ctx
    )

    assert calls == [
        ("next-agent", {"parent_run_id": "p-1", "artifacts": ["a.md"]}, "parent-p-1")
    ]
    assert result.status == "applied"
    assert result.result["run_id"] == str(child)
    assert result.result["url"].endswith(f"/runs/{child}")


async def test_a_child_that_failed_to_start_fails_the_action():
    ctx = ApplyContext(
        run_id="p-1",
        agent="first-agent",
        download_artifact=_no_download,
        hackbot_client=_FakeHackbotClient([], uuid.uuid4(), status="failed"),
    )

    result = await agents_handler.StartRunHandler().apply({"agent": "next-agent"}, ctx)

    assert result.status == "failed"
    assert "failed to start" in result.error
