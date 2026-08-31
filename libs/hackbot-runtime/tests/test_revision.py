"""Tests for checking the source tree out at a Phabricator revision.

Only the Conduit HTTP call is faked. moz-phab's real stack walk, argument
handling and patch application run, against a real git repository, so what is
under test is what actually runs in production.
"""

import os
import subprocess
from pathlib import Path

import pytest
from hackbot_runtime import revision

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


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


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
            "title": f"Revision {rev_id}",
            "summary": "",
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
    raw_diffs: dict[int, str] | None = None,
):
    """Serve moz-phab's Conduit calls from in-memory fixtures.

    Only `conduit.call` is replaced, so `get_revisions`/`get_diffs`, their
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
                        "fields": {
                            "refs": [{"type": "base", "identifier": base}],
                            "dateCreated": 0,
                        },
                        "attachments": {"commits": {"commits": []}},
                    }
                    for rev in revisions.values()
                    if rev["fields"]["diffPHID"] in params["constraints"]["phids"]
                ]
            }
        if method == "differential.getrawdiff":
            diff_id = params["diffID"]
            if raw_diffs is not None:
                return raw_diffs[diff_id // 10]
            return f"diff for diff {diff_id}\n"
        if method == "diffusion.querycommits":
            return querycommits
        raise AssertionError(f"unexpected Conduit method {method}")

    monkeypatch.setattr(conduit, "call", _call)
    return seen


# --- Finding the base --------------------------------------------------- #


async def _base_of(monkeypatch, revisions, target, **kwargs):
    seen = _fake_conduit(monkeypatch, revisions, **kwargs)
    revision._load_mozphab()
    return revision._resolve_base(target, BROKER), seen


async def test_base_of_an_unstacked_revision_is_its_own(monkeypatch):
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})

    base, _ = await _base_of(monkeypatch, revisions, 42)

    assert base == revision.StackBase(commit=BASE, parent_id=None)


async def test_base_of_a_stacked_revision_comes_from_the_bottom(monkeypatch):
    # D44 sits on D43 sits on D42. D44's own recorded base is D43's local
    # commit, which never landed, so the base has to come from D42.
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43), 44: _revision(44)},
        {42: [], 43: [42], 44: [43]},
    )

    base, _ = await _base_of(monkeypatch, revisions, 44)

    assert base.commit == BASE
    # The direct parent, so moz-phab can lay down everything below the target.
    assert base.parent_id == 43


async def test_abandoned_ancestors_are_not_the_bottom(monkeypatch):
    revisions = _with_stack_graph(
        {42: _revision(42, status="abandoned"), 43: _revision(43), 44: _revision(44)},
        {42: [], 43: [42], 44: [43]},
    )

    base, _ = await _base_of(monkeypatch, revisions, 44)

    # D42 is abandoned, so moz-phab will not apply it and D43 is the bottom.
    assert base.parent_id == 43


async def test_base_lookup_rejects_a_non_linear_stack(monkeypatch):
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43), 44: _revision(44)},
        {42: [], 43: [], 44: [42, 43]},
    )

    with pytest.raises(RuntimeError, match="more than one parent"):
        await _base_of(monkeypatch, revisions, 44)


async def test_base_lookup_expands_an_abbreviated_commit(monkeypatch):
    # git can only fetch a full object id, and moz-phab records a short hash.
    short = BASE[:12]
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})

    base, _ = await _base_of(
        monkeypatch,
        revisions,
        42,
        base=short,
        querycommits={
            "identifierMap": {short: "PHID-CMIT-1"},
            "data": {"PHID-CMIT-1": {"identifier": BASE}},
        },
    )

    assert base.commit == BASE


async def test_base_lookup_reports_an_unresolvable_commit(monkeypatch):
    from phabricator_client import UnresolvedCommitError

    short = "69706d7a081e"
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})

    with pytest.raises(UnresolvedCommitError, match=short):
        await _base_of(
            monkeypatch,
            revisions,
            42,
            base=short,
            querycommits={"identifierMap": {}, "data": {}},
        )


async def test_base_lookup_raises_when_the_revision_is_missing(monkeypatch):
    with pytest.raises(RuntimeError, match="D42 not found"):
        await _base_of(monkeypatch, {}, 42)


async def test_base_lookup_talks_to_the_proxy_with_a_placeholder_token(monkeypatch):
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})

    _, seen = await _base_of(monkeypatch, revisions, 42)

    # Calls go to the broker's proxy mount carrying the placeholder token it
    # swaps out — the agent never holds a real Conduit key.
    assert seen["api_url"] == f"{BROKER}/phabricator/api/"
    assert seen["token"] == revision._PROXY_API_TOKEN
    # And nothing is left configured for the rest of the run.
    assert conduit.repo is None
    assert revision._TOKEN_ENV not in os.environ


async def test_base_lookup_uses_only_allow_listed_conduit_methods(monkeypatch):
    # The broker refuses anything outside its allow list, so a new Conduit call
    # here has to be a deliberate change on both sides.
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43)}, {42: [], 43: [42]}
    )

    _, seen = await _base_of(
        monkeypatch,
        revisions,
        43,
        base=BASE[:12],
        querycommits={
            "identifierMap": {BASE[:12]: "PHID-CMIT-1"},
            "data": {"PHID-CMIT-1": {"identifier": BASE}},
        },
    )

    assert set(seen["methods"]) <= {
        "differential.revision.search",
        "differential.diff.search",
        "differential.getrawdiff",
        "diffusion.querycommits",
    }


# --- Applying the revisions with moz-phab -------------------------------- #


def _repo_at_base(tmp_path: Path) -> Path:
    """A real git repo standing in for the prepared checkout.

    Carries a committed `.arcconfig` like firefox does: moz-phab's Repository
    refuses to start without one, and it has to be committed because
    `moz-phab patch` refuses to run on a dirty worktree.
    """
    repo = tmp_path / "firefox"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".arcconfig").write_text(
        '{"phabricator.uri": "https://phabricator.services.mozilla.com/"}\n'
    )
    (repo / "file.txt").write_text("base\n")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=Base",
        "-c",
        "user.email=base@example.com",
        "commit",
        "-q",
        "-m",
        "base commit",
    )
    return repo


def _diff(old: str, new: str) -> str:
    """A unified diff turning `file.txt` from `old` into `new`."""
    return (
        "diff --git a/file.txt b/file.txt\n"
        "--- a/file.txt\n"
        "+++ b/file.txt\n"
        "@@ -1 +1 @@\n"
        f"-{old}\n"
        f"+{new}\n"
    )


async def test_unstacked_revision_is_applied_uncommitted(monkeypatch, tmp_path):
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})
    _fake_conduit(monkeypatch, revisions, raw_diffs={42: _diff("base", "from D42")})
    ctx = _FakeCtx(repo)

    await revision.checkout_revision(ctx, 42, BROKER)

    assert ctx.prepared_ref == BASE
    assert (repo / "file.txt").read_text() == "from D42\n"
    # Nothing below it, so no extra commit and the agent's diff starts here.
    assert _git(repo, "log", "--format=%s").splitlines() == ["base commit"]
    assert "file.txt" in _git(repo, "status", "--porcelain")


async def test_stacked_revision_gets_its_ancestors_committed_first(
    monkeypatch, tmp_path
):
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43), 44: _revision(44)},
        {42: [], 43: [42], 44: [43]},
    )
    _fake_conduit(
        monkeypatch,
        revisions,
        raw_diffs={
            42: _diff("base", "from D42"),
            43: _diff("from D42", "from D43"),
            44: _diff("from D43", "from D44"),
        },
    )
    ctx = _FakeCtx(repo)

    await revision.checkout_revision(ctx, 44, BROKER)

    # The whole stack was replayed in order onto the landed base.
    assert (repo / "file.txt").read_text() == "from D44\n"
    # D42 and D43 became the base the agent starts from...
    assert _git(repo, "log", "--format=%s").splitlines() == [
        "Revisions below D44",
        "base commit",
    ]
    # ...and only D44 is left in the working tree, so the diff hackbot submits
    # covers D44 plus the agent's edits, not the whole stack.
    assert "file.txt" in _git(repo, "status", "--porcelain")
    assert ctx.rebased_base is True


async def test_descendants_of_the_target_are_not_applied(monkeypatch, tmp_path):
    # D44 has a child D45. Patching D43 to lay down the base would offer to
    # patch the full stack, which would drag D45 in; hackbot declines.
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph(
        {
            42: _revision(42),
            43: _revision(43),
            44: _revision(44),
            45: _revision(45),
        },
        {42: [], 43: [42], 44: [43], 45: [44]},
    )
    _fake_conduit(
        monkeypatch,
        revisions,
        raw_diffs={
            42: _diff("base", "from D42"),
            43: _diff("from D42", "from D43"),
            44: _diff("from D43", "from D44"),
            45: _diff("from D44", "from D45"),
        },
    )

    await revision.checkout_revision(_FakeCtx(repo), 44, BROKER)

    assert (repo / "file.txt").read_text() == "from D44\n"


def test_an_unexpected_moz_phab_question_fails_instead_of_hanging():
    # Nothing is attached to stdin in a container, so a question hackbot has no
    # answer for must raise rather than block forever.
    from mozphab.commands import patch as patch_command

    original = patch_command.prompt
    with revision._decline_descendants(patch_command):
        assert patch_command.prompt("Would you like to patch the full stack?.") == "No"
        with pytest.raises(RuntimeError, match="cannot give"):
            patch_command.prompt("Something new?", ["Yes", "No"])
    assert patch_command.prompt is original


async def test_a_diff_that_does_not_apply_fails_the_run(monkeypatch, tmp_path):
    from mozphab.exceptions import CommandError

    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})
    _fake_conduit(
        monkeypatch, revisions, raw_diffs={42: _diff("not what is there", "whatever")}
    )

    # moz-phab's own failure surfaces rather than being swallowed, so the run
    # fails instead of handing the agent a tree that is not the revision.
    with pytest.raises(CommandError, match="git"):
        await revision.checkout_revision(_FakeCtx(repo), 42, BROKER)

    assert (repo / "file.txt").read_text() == "base\n"


async def test_apply_leaves_the_checkouts_arcconfig_alone(monkeypatch, tmp_path):
    # The URL override happens in memory: rewriting `.arcconfig` would dirty
    # the worktree, which `moz-phab patch` refuses to work on.
    repo = _repo_at_base(tmp_path)
    before = (repo / ".arcconfig").read_text()
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})
    _fake_conduit(monkeypatch, revisions, raw_diffs={42: _diff("base", "from D42")})

    await revision.checkout_revision(_FakeCtx(repo), 42, BROKER)

    assert (repo / ".arcconfig").read_text() == before


def test_proxied_conduit_restores_a_pre_existing_token(monkeypatch):
    monkeypatch.setenv(revision._TOKEN_ENV, "api-someone-elses")
    with revision._conduit_via_proxy(conduit, revision._ProxyRepo("http://x/api/")):
        assert os.environ[revision._TOKEN_ENV] == revision._PROXY_API_TOKEN
    assert os.environ[revision._TOKEN_ENV] == "api-someone-elses"


def test_revision_holds_no_conduit_client_of_its_own():
    # Guard against reintroducing an in-agent Conduit client, which would need
    # a key: everything goes through the broker's proxy, which owns the key.
    assert not hasattr(revision, "PhabricatorClient")
