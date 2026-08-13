"""Tests for checking the source tree out at a Phabricator revision.

These drive moz-phab's real Conduit client and stack-graph helpers, with only
the HTTP call itself faked, so the stack walk under test is the one that
actually runs in production.
"""

import os
from pathlib import Path

import pytest
from hackbot_runtime import revision
from phabricator_client import UnresolvedCommitError

pytest.importorskip("mozphab", reason="needs the hackbot-runtime[phabricator] extra")

from mozphab.conduit import conduit  # noqa: E402
from mozphab.simplecache import cache  # noqa: E402

BROKER = "http://127.0.0.1:8765"
BASE = "1" * 40


@pytest.fixture(autouse=True)
def _clear_mozphab_cache():
    # moz-phab memoises revisions and diffs process-wide.
    cache.reset()
    yield
    cache.reset()


class _FakeCtx:
    """Stand-in for HackbotContext: records the ref and base bookkeeping."""

    def __init__(self, repo: Path):
        self._repo = repo
        self.prepared_ref = None
        self.rebased_base = False

    async def prepare_repo(
        self, ref: str | None = None, depth: int | None = None
    ) -> Path:
        self.prepared_ref = ref
        return self._repo

    def record_source_base(self) -> None:
        self.rebased_base = True


def _revision(rev_id: int, *, status: str = "needs-review"):
    """A `differential.revision.search` entry, keyed by id for readability."""
    return {
        "id": rev_id,
        "phid": f"PHID-DREV-{rev_id}",
        "fields": {
            "diffPHID": f"PHID-DIFF-{rev_id}",
            "status": {"value": status},
            "stackGraph": {},
        },
    }


def _with_stack_graph(revisions: dict[int, dict], graph: dict[int, list[int]]):
    """Stamp the same stack graph onto every revision, as Conduit does."""
    phid_graph = {
        f"PHID-DREV-{child}": [f"PHID-DREV-{p}" for p in parents]
        for child, parents in graph.items()
    }
    for rev in revisions.values():
        rev["fields"]["stackGraph"] = phid_graph
    return revisions


def _fake_conduit(
    monkeypatch,
    revisions: dict[int, dict],
    *,
    base: str = BASE,
    querycommits: dict | None = None,
):
    """Serve moz-phab's Conduit calls from in-memory fixtures.

    Only `conduit.call` is replaced, so `get_revisions`/`get_diffs` and their
    caching, request shaping and result ordering all still run for real.

    Returns a dict recording how the client was configured at call time.
    """
    seen: dict = {"methods": []}

    def _call(method, params, api_token=None):
        seen["methods"].append(method)
        seen.setdefault("api_url", conduit.repo.api_url)
        seen.setdefault("token", os.environ.get(revision._TOKEN_ENV))
        if method == "differential.revision.search":
            constraints = params["constraints"]
            if "ids" in constraints:
                wanted = {
                    rev["phid"]
                    for rev_id, rev in revisions.items()
                    if rev_id in constraints["ids"]
                }
            else:
                wanted = set(constraints["phids"])
            return {"data": [r for r in revisions.values() if r["phid"] in wanted]}
        if method == "differential.diff.search":
            return {
                "data": [
                    {
                        "id": rev["id"] * 10,
                        "phid": rev["fields"]["diffPHID"],
                        "fields": {"refs": [{"type": "base", "identifier": base}]},
                    }
                    for rev in revisions.values()
                    if rev["fields"]["diffPHID"] in params["constraints"]["phids"]
                ]
            }
        if method == "differential.getrawdiff":
            return f"diff for diff {params['diffID']}\n"
        if method == "diffusion.querycommits":
            return querycommits
        raise AssertionError(f"unexpected Conduit method {method}")

    monkeypatch.setattr(conduit, "call", _call)
    return seen


def _fake_git(monkeypatch, *, returncode=0, stderr=b""):
    """Record `git apply` invocations instead of touching a real repo."""
    applied = []

    def _run(cmd, input=None, capture_output=False):
        applied.append((cmd, input))
        return type("R", (), {"returncode": returncode, "stderr": stderr})()

    monkeypatch.setattr(revision.subprocess, "run", _run)
    return applied


def _fake_commit(monkeypatch):
    commits = []
    monkeypatch.setattr(
        revision.changes, "commit_all", lambda repo, message: commits.append(message)
    )
    return commits


async def test_checkout_applies_the_diff_at_its_base(monkeypatch, tmp_path):
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})
    _fake_conduit(monkeypatch, revisions)
    applied = _fake_git(monkeypatch)
    commits = _fake_commit(monkeypatch)
    ctx = _FakeCtx(tmp_path)

    await revision.checkout_revision(ctx, 42, BROKER)

    assert ctx.prepared_ref == BASE
    assert [cmd for cmd, _ in applied] == [["git", "-C", str(tmp_path), "apply"]]
    assert applied[0][1] == b"diff for diff 420\n"
    # Nothing below it, so nothing to commit and the base stays where it was.
    assert commits == []
    assert ctx.rebased_base is False


async def test_checkout_replays_unlanded_ancestors_first(monkeypatch, tmp_path):
    # D44 sits on D43 sits on D42. D44's own recorded base is D43's local
    # commit, which never landed — the whole stack has to be replayed from the
    # landed commit under D42.
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43), 44: _revision(44)},
        {42: [], 43: [42], 44: [43]},
    )
    _fake_conduit(monkeypatch, revisions)
    applied = _fake_git(monkeypatch)
    commits = _fake_commit(monkeypatch)
    ctx = _FakeCtx(tmp_path)

    await revision.checkout_revision(ctx, 44, BROKER)

    assert ctx.prepared_ref == BASE
    # Oldest first, and the target's diff applied last.
    assert [patch for _, patch in applied] == [
        b"diff for diff 420\n",
        b"diff for diff 430\n",
        b"diff for diff 440\n",
    ]
    # The ancestors become the base, so what the agent submits later covers
    # D44 alone rather than the whole stack.
    assert commits == ["Stack below D44: D42, D43"]
    assert ctx.rebased_base is True


async def test_checkout_skips_abandoned_ancestors(monkeypatch, tmp_path):
    revisions = _with_stack_graph(
        {
            42: _revision(42),
            43: _revision(43, status="abandoned"),
            44: _revision(44),
        },
        {42: [], 43: [42], 44: [43]},
    )
    _fake_conduit(monkeypatch, revisions)
    applied = _fake_git(monkeypatch)
    commits = _fake_commit(monkeypatch)
    ctx = _FakeCtx(tmp_path)

    await revision.checkout_revision(ctx, 44, BROKER)

    assert [patch for _, patch in applied] == [
        b"diff for diff 420\n",
        b"diff for diff 440\n",
    ]
    assert commits == ["Stack below D44: D42"]


async def test_checkout_rejects_a_non_linear_stack(monkeypatch, tmp_path):
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43), 44: _revision(44)},
        {42: [], 43: [], 44: [42, 43]},
    )
    _fake_conduit(monkeypatch, revisions)
    _fake_git(monkeypatch)
    ctx = _FakeCtx(tmp_path)

    with pytest.raises(RuntimeError, match="more than one parent"):
        await revision.checkout_revision(ctx, 44, BROKER)


async def test_checkout_expands_an_abbreviated_base(monkeypatch, tmp_path):
    # git can only fetch a full object id, and moz-phab records a short hash.
    short = BASE[:12]
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})
    _fake_conduit(
        monkeypatch,
        revisions,
        base=short,
        querycommits={
            "identifierMap": {short: "PHID-CMIT-1"},
            "data": {"PHID-CMIT-1": {"identifier": BASE}},
        },
    )
    _fake_git(monkeypatch)
    ctx = _FakeCtx(tmp_path)

    await revision.checkout_revision(ctx, 42, BROKER)

    assert ctx.prepared_ref == BASE


async def test_checkout_reports_an_unresolvable_base(monkeypatch, tmp_path):
    short = "69706d7a081e"
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})
    _fake_conduit(
        monkeypatch,
        revisions,
        base=short,
        querycommits={"identifierMap": {}, "data": {}},
    )
    _fake_git(monkeypatch)
    ctx = _FakeCtx(tmp_path)

    with pytest.raises(UnresolvedCommitError, match=short):
        await revision.checkout_revision(ctx, 42, BROKER)


async def test_checkout_names_the_revision_whose_diff_failed(monkeypatch, tmp_path):
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43)}, {42: [], 43: [42]}
    )
    _fake_conduit(monkeypatch, revisions)
    _fake_git(monkeypatch, returncode=1, stderr=b"patch does not apply")
    _fake_commit(monkeypatch)
    ctx = _FakeCtx(tmp_path)

    with pytest.raises(RuntimeError, match="Could not apply diff for D42"):
        await revision.checkout_revision(ctx, 43, BROKER)


async def test_checkout_raises_when_the_revision_is_missing(monkeypatch, tmp_path):
    _fake_conduit(monkeypatch, {})
    ctx = _FakeCtx(tmp_path)

    with pytest.raises(RuntimeError, match="D42 not found"):
        await revision.checkout_revision(ctx, 42, BROKER)


async def test_conduit_talks_to_the_brokers_proxy_with_a_placeholder_token(
    monkeypatch, tmp_path
):
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})
    seen = _fake_conduit(monkeypatch, revisions)
    _fake_git(monkeypatch)

    await revision.checkout_revision(_FakeCtx(tmp_path), 42, BROKER)

    # Every call went to the broker, carrying the placeholder token the broker
    # swaps out — the agent never holds a real Conduit key.
    assert seen["api_url"] == f"{BROKER}/api/"
    assert seen["token"] == revision._PROXY_API_TOKEN
    # And nothing was left configured for the rest of the run.
    assert conduit.repo is None
    assert revision._TOKEN_ENV not in os.environ


async def test_only_allow_listed_conduit_methods_are_used(monkeypatch, tmp_path):
    # The broker refuses anything outside its allow list, so a new Conduit call
    # here has to be a deliberate change on both sides.
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43)}, {42: [], 43: [42]}
    )
    seen = _fake_conduit(
        monkeypatch,
        revisions,
        base=BASE[:12],
        querycommits={
            "identifierMap": {BASE[:12]: "PHID-CMIT-1"},
            "data": {"PHID-CMIT-1": {"identifier": BASE}},
        },
    )
    _fake_git(monkeypatch)
    _fake_commit(monkeypatch)

    await revision.checkout_revision(_FakeCtx(tmp_path), 43, BROKER)

    assert set(seen["methods"]) <= {
        "differential.revision.search",
        "differential.diff.search",
        "differential.getrawdiff",
        "diffusion.querycommits",
    }


def test_proxied_conduit_restores_a_pre_existing_token(monkeypatch):
    monkeypatch.setenv(revision._TOKEN_ENV, "api-someone-elses")
    with revision._proxied_conduit(conduit, BROKER):
        assert os.environ[revision._TOKEN_ENV] == revision._PROXY_API_TOKEN
    assert os.environ[revision._TOKEN_ENV] == "api-someone-elses"


def test_revision_holds_no_conduit_client_of_its_own():
    # Guard against reintroducing an in-agent Conduit client, which would need
    # a key: everything goes through the broker's proxy, which owns the key.
    assert not hasattr(revision, "PhabricatorClient")
