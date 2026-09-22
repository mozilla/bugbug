"""Tests for the agent entrypoint's handoff to the runtime.

``main`` is the seam between ``HackbotContext`` and ``run_uplift``. The context
is stubbed rather than mocked, so the contract is pinned by assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hackbot_agents.uplift_merge_conflict_resolver import __main__ as entrypoint
from hackbot_agents.uplift_merge_conflict_resolver.models import (
    GitSource,
    PhabricatorSource,
)

REPO_PATH = Path("/workspace/firefox")


class StubContext:
    """A stand-in ``HackbotContext`` recording how source is prepared."""

    def __init__(self) -> None:
        self.prepare_calls: list[dict] = []
        self.log_path = Path("/artifacts/agent.log")

    async def prepare_repo(self, ref: str | None = None, depth: int | None = None):
        self.prepare_calls.append({"ref": ref, "depth": depth})
        return REPO_PATH

    def publish_file(self, name: str, path: Path, content_type: str | None) -> str:
        return f"published://{name}"


@pytest.fixture
def recorded_run(monkeypatch) -> dict:
    """Capture the keyword arguments the entrypoint hands to ``run_uplift``."""
    captured: dict = {}

    async def fake_run_uplift(**kwargs):
        captured.update(kwargs)
        return "result"

    monkeypatch.setattr(entrypoint, "run_uplift", fake_run_uplift)
    return captured


@pytest.fixture
def agent_env(monkeypatch) -> None:
    monkeypatch.setenv("TARGET_BRANCH", "beta")
    monkeypatch.setenv("SOURCES", '[{"kind": "git", "commit": "' + "a" * 40 + '"}]')
    monkeypatch.setenv("BUGBUG_MCP_URL", "http://localhost:8080/mcp")
    monkeypatch.setenv("BROKER_URL", "http://localhost:8765")
    for name in (
        "BUG_ID",
        "MODEL",
        "MAX_TURNS",
        "EFFORT",
        "SOURCE_REF",
        "TARGET_COMMIT",
    ):
        monkeypatch.delenv(name, raising=False)


async def test_the_handoff_to_the_agent(agent_env, recorded_run):
    """One prepare, pinned to the branch, and the agent runs against its path."""
    ctx = StubContext()

    await entrypoint.main(ctx)

    assert ctx.prepare_calls == [{"ref": "beta", "depth": None}], (
        "The checkout is prepared exactly once, pinned to `target_branch`."
    )
    assert recorded_run["source_repo"] == REPO_PATH, (
        "`run_uplift` receives the path `prepare_repo` returned."
    )
    assert recorded_run["target_branch"] == "beta", (
        "`target_branch` is forwarded so the prompt names the branch."
    )
    assert [source.commit for source in recorded_run["sources"]] == ["a" * 40], (
        "Sources parsed from the environment reach the agent in order."
    )


async def test_a_pinned_target_commit_is_what_gets_checked_out(
    monkeypatch, agent_env, recorded_run
):
    """A branch name moves, so a caller reproducing an uplift can pin the commit."""
    monkeypatch.setenv("TARGET_COMMIT", "f" * 40)
    ctx = StubContext()

    await entrypoint.main(ctx)

    assert ctx.prepare_calls == [{"ref": "f" * 40, "depth": None}], (
        "The pinned commit should be checked out instead of the branch tip."
    )
    assert recorded_run["target_branch"] == "beta", (
        "The branch is still reported, since it is what the uplift targets."
    )


async def test_inputs_treat_compose_empty_strings_as_absent(monkeypatch, agent_env):
    """A local `docker compose` run passes every unset optional input as ``""``."""
    for name in ("BUG_ID", "MODEL", "MAX_TURNS", "EFFORT"):
        monkeypatch.setenv(name, "")

    inputs = entrypoint.AgentInputs()

    assert (inputs.bug_id, inputs.model, inputs.max_turns, inputs.effort) == (
        None,
        None,
        None,
        None,
    ), "An empty env var should fall back to the default, not fail validation."


async def test_inputs_parse_mixed_sources_from_the_environment(monkeypatch, agent_env):
    """The real input path: one env var holding a JSON list of mixed sources."""
    monkeypatch.setenv(
        "SOURCES",
        '[{"kind": "git", "commit": "abc"}, '
        '{"kind": "phabricator", "revision_id": 99, "diff_id": 500}]',
    )
    monkeypatch.setenv("BUG_ID", "1234567")

    inputs = entrypoint.AgentInputs()

    assert inputs.bug_id == 1234567, "`BUG_ID` should map to `bug_id`."
    assert [type(source) for source in inputs.sources] == [
        GitSource,
        PhabricatorSource,
    ], "`SOURCES` should parse to the discriminated source kinds, in order."
    assert inputs.sources[1].diff_id == 500, "A pinned `diff_id` should survive."
