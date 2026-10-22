"""Tests for the entry point: a skipped bug costs neither a checkout nor a model turn."""

import asyncio
import threading

import pytest
from conftest import NOW
from hackbot_agents.gecko_triage import __main__ as entry
from hackbot_agents.gecko_triage.preflight import BugUnreadable
from hackbot_agents.gecko_triage.state import TriageState

BUG = 9000001


class FakeContext:
    def __init__(self):
        self.prepared = False
        self.repo_path = "/repo"
        self.log_path = "/log"
        self.actions = object()

    async def prepare_repo(self):
        self.prepared = True


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setenv("BUG_ID", str(BUG))
    monkeypatch.setenv("BROKER_URL", "http://127.0.0.1:8765/")
    monkeypatch.setattr(entry, "require_toolkit", lambda _dir: None)
    calls = {}

    def use_state(state):
        async def fetch_state(server, bug, **kwargs):
            calls["fetch_state"] = (server, bug, kwargs)
            return state

        monkeypatch.setattr(entry, "fetch_state", fetch_state)

    async def resolve_index_revision():
        calls["searchfox"] = True
        return "abc"

    async def find_candidates(server, bug):
        calls["dupes"] = True
        return []

    async def run_gecko_triage(**kwargs):
        calls["run"] = kwargs
        return "ran"

    monkeypatch.setattr(entry, "resolve_index_revision", resolve_index_revision)
    monkeypatch.setattr(entry, "find_candidates", find_candidates)
    monkeypatch.setattr(entry, "run_gecko_triage", run_gecko_triage)
    return use_state, calls


async def test_a_restricted_bug_is_skipped_before_the_checkout_and_the_model(
    wired, bug_new, comments_new
):
    use_state, calls = wired
    use_state(
        TriageState(
            bug=dict(bug_new, groups=["core-security"]), comments=comments_new, now=NOW
        )
    )
    ctx = FakeContext()
    result = await entry.main(ctx)
    assert result.decision == "skipped"
    assert result.num_turns == 0 and result.total_cost_usd == 0.0
    assert "restricted" in result.result
    assert result.agent_version
    assert not ctx.prepared
    assert set(calls) == {"fetch_state"}


async def test_a_bug_the_broker_withholds_is_skipped_not_failed(wired):
    use_state, calls = wired
    use_state(TriageState(bug={"id": BUG}, now=NOW, restricted=True))
    ctx = FakeContext()
    result = await entry.main(ctx)
    assert result.decision == "skipped"
    assert "restricted" in result.result
    assert not ctx.prepared
    assert set(calls) == {"fetch_state"}


async def test_an_unreadable_bug_fails_before_the_checkout(wired):
    use_state, calls = wired
    use_state(None)
    ctx = FakeContext()
    with pytest.raises(BugUnreadable):
        await entry.main(ctx)
    assert not ctx.prepared
    assert set(calls) == {"fetch_state"}


async def test_a_bug_to_triage_reaches_the_model_after_the_checkout(
    wired, new_state, monkeypatch
):
    monkeypatch.setenv("MAX_TURNS", "12")
    monkeypatch.setenv("MAX_BUDGET_USD", "3.5")
    use_state, calls = wired
    use_state(new_state)
    ctx = FakeContext()
    assert await entry.main(ctx) == "ran"
    assert ctx.prepared
    server, bug, kwargs = calls["fetch_state"]
    assert kwargs == {}
    assert server == {"type": "http", "url": "http://127.0.0.1:8765/mcp"}
    assert bug == BUG
    assert calls["run"]["bug"] == BUG
    assert calls["run"]["state"] is new_state
    assert calls["run"]["searchfox_rev"] == "abc"
    assert calls["run"]["source_repo"] == "/repo"
    assert calls["run"]["max_turns"] == 12
    assert calls["run"]["max_budget_usd"] == 3.5


async def test_the_checkout_runs_beside_the_duplicate_search(
    wired, new_state, monkeypatch
):
    use_state, calls = wired
    use_state(new_state)
    searching, cloning = threading.Event(), threading.Event()

    class BlockingContext(FakeContext):
        async def prepare_repo(self):
            # A blocking clone, as `ensure_source_repo` does.
            cloning.set()
            self.prepared = searching.wait(5)

    async def find_candidates(server, bug):
        searching.set()
        for _ in range(200):
            if cloning.is_set():
                break
            await asyncio.sleep(0.01)
        calls["cloning_during_search"] = cloning.is_set()
        return []

    monkeypatch.setattr(entry, "find_candidates", find_candidates)
    ctx = BlockingContext()
    assert await entry.main(ctx) == "ran"
    assert calls["cloning_during_search"] and ctx.prepared
