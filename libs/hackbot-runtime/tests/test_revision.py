"""Tests for checking the source tree out at a Phabricator revision.

Only the Conduit HTTP call is faked. The stack walk, the patch application and
the commit boundaries are the real ones, run against a real git repository.
"""

import json
import subprocess
from pathlib import Path

import pytest
from hackbot_runtime import revision
from phabricator_client import UnresolvedCommitError
from phabricator_client import client as client_module

BROKER = "http://127.0.0.1:8765"
BASE = "1" * 40


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
            "title": f"Do the thing in D{rev_id}",
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
    with_authors: bool = True,
):
    """Serve Conduit over a stubbed httpx, recording what was asked for.

    Everything above the HTTP call — PhabricatorClient, the stack walk, the
    ordering — is the real code path.
    """
    seen: dict = {"methods": [], "urls": []}

    def _result(method: str, params: dict):
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
        if method == "differential.querydiffs":
            (rev_id,) = params["revisionIDs"]
            if rev_id not in revisions:
                return {}
            diff = {"id": rev_id * 10, "sourceControlBaseRevision": base}
            if with_authors:
                diff["authorName"] = f"Author {rev_id}"
                diff["authorEmail"] = f"author{rev_id}@example.com"
            return {str(rev_id * 10): diff}
        if method == "differential.getrawdiff":
            diff_id = params["diffID"]
            if raw_diffs is not None:
                return raw_diffs[diff_id // 10]
            return f"diff for diff {diff_id}\n"
        if method == "diffusion.querycommits":
            return querycommits
        raise AssertionError(f"unexpected Conduit method {method}")

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data=None):
            method = url.rsplit("/", 1)[-1]
            seen["methods"].append(method)
            seen["urls"].append(url)
            params = json.loads(data["params"])
            seen.setdefault("token", params["__conduit__"]["token"])
            return _FakeResponse({"result": _result(method, params)})

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)
    return seen


# --- Resolving the stack ------------------------------------------------- #


async def _stack_of(monkeypatch, revisions, target, **kwargs):
    seen = _fake_conduit(monkeypatch, revisions, **kwargs)
    client = revision.PhabricatorClient(
        revision.PhabricatorSettings(
            url=f"{BROKER}/phabricator", api_key=revision._PROXY_API_TOKEN
        )
    )
    return await revision._resolve_stack(client, target), seen


async def test_an_unstacked_revision_has_no_ancestors(monkeypatch):
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})

    stack, _ = await _stack_of(monkeypatch, revisions, 42)

    assert stack.base_commit == BASE
    assert stack.ancestors == []
    assert stack.target.revision_id == 42


async def test_a_stacked_revision_collects_its_ancestors_oldest_first(monkeypatch):
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43), 44: _revision(44)},
        {42: [], 43: [42], 44: [43]},
    )

    stack, _ = await _stack_of(monkeypatch, revisions, 44)

    assert [p.revision_id for p in stack.ancestors] == [42, 43]
    assert stack.target.revision_id == 44
    # The base comes from the bottom of the stack, not the target: D44's own
    # base would be D43's unlanded local commit.
    assert stack.base_commit == BASE


async def test_abandoned_ancestors_are_dropped(monkeypatch):
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43, status="abandoned"), 44: _revision(44)},
        {42: [], 43: [42], 44: [43]},
    )

    stack, _ = await _stack_of(monkeypatch, revisions, 44)

    assert [p.revision_id for p in stack.ancestors] == [42]


async def test_a_non_linear_stack_is_rejected(monkeypatch):
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43), 44: _revision(44)},
        {42: [], 43: [], 44: [42, 43]},
    )

    with pytest.raises(RuntimeError, match="non-linear"):
        await _stack_of(monkeypatch, revisions, 44)


def test_a_cycle_in_the_stack_graph_does_not_hang():
    # Conduit misbehaving rather than anything real, but a walk that loops
    # forever would be a much worse failure than a short one.
    graph = {"A": ["B"], "B": ["C"], "C": ["A"]}

    assert revision._ancestor_phids(graph, "A") == ["B", "C"]


async def test_an_abbreviated_base_is_expanded(monkeypatch):
    # git can only fetch a full object id, and moz-phab records a short hash.
    short = BASE[:12]
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})

    stack, _ = await _stack_of(
        monkeypatch,
        revisions,
        42,
        base=short,
        querycommits={
            "identifierMap": {short: "PHID-CMIT-1"},
            "data": {"PHID-CMIT-1": {"identifier": BASE}},
        },
    )

    assert stack.base_commit == BASE


async def test_an_unresolvable_base_is_reported(monkeypatch):
    short = "69706d7a081e"
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})

    with pytest.raises(UnresolvedCommitError, match=short):
        await _stack_of(
            monkeypatch,
            revisions,
            42,
            base=short,
            querycommits={"identifierMap": {}, "data": {}},
        )


async def test_a_missing_revision_is_reported(monkeypatch):
    with pytest.raises(RuntimeError, match="D42 not found"):
        await _stack_of(monkeypatch, {}, 42)


async def test_conduit_goes_through_the_brokers_proxy(monkeypatch):
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})

    _, seen = await _stack_of(monkeypatch, revisions, 42)

    assert all(url.startswith(f"{BROKER}/phabricator/api/") for url in seen["urls"])
    # A placeholder the proxy throws away: the agent holds no real key.
    assert seen["token"] == revision._PROXY_API_TOKEN


async def test_only_allow_listed_conduit_methods_are_used(monkeypatch):
    # The broker refuses anything outside its allow list, so a new Conduit call
    # here has to be a deliberate change on both sides.
    from phabricator_proxy import READ_ONLY_METHODS

    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43)}, {42: [], 43: [42]}
    )

    _, seen = await _stack_of(
        monkeypatch,
        revisions,
        43,
        base=BASE[:12],
        querycommits={
            "identifierMap": {BASE[:12]: "PHID-CMIT-1"},
            "data": {"PHID-CMIT-1": {"identifier": BASE}},
        },
    )

    assert set(seen["methods"]) <= set(READ_ONLY_METHODS)


# --- Applying the stack --------------------------------------------------- #


def _repo_at_base(tmp_path: Path) -> Path:
    """A real git repo standing in for the prepared checkout."""
    repo = tmp_path / "firefox"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
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


@pytest.mark.parametrize(
    "graph, target, committed_head",
    [
        ({42: []}, 42, "base\n"),
        ({42: [], 43: [42]}, 43, "from D42\n"),
        ({42: [], 43: [42], 44: [43]}, 44, "from D43\n"),
    ],
    ids=["unstacked", "one-ancestor", "two-ancestors"],
)
async def test_only_the_target_is_left_uncommitted(
    monkeypatch, tmp_path, graph, target, committed_head
):
    # The invariant the submit path depends on, and it must not vary with the
    # depth of the stack: everything below the target is committed, and exactly
    # the target's own change is left in the working tree.
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph({r: _revision(r) for r in graph}, graph)
    contents = {42: "from D42", 43: "from D43", 44: "from D44"}
    _fake_conduit(
        monkeypatch,
        revisions,
        raw_diffs={
            42: _diff("base", contents[42]),
            43: _diff(contents[42], contents[43]),
            44: _diff(contents[43], contents[44]),
        },
    )
    ctx = _FakeCtx(repo)

    await revision.checkout_revision(ctx, target, BROKER)

    assert ctx.prepared_ref == BASE
    # HEAD holds the revisions below the target, and nothing of the target.
    assert _git(repo, "show", "HEAD:file.txt") == committed_head
    # The target's change is present, and uncommitted.
    assert (repo / "file.txt").read_text() == f"{contents[target]}\n"
    assert _git(repo, "diff", "HEAD", "--name-only").split() == ["file.txt"]
    assert ctx.rebased_base is True


async def test_each_ancestor_gets_its_own_commit(monkeypatch, tmp_path):
    # One squashed lump would leave the agent unable to tell which change came
    # from which revision, which is exactly what it reads `git log` for.
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

    await revision.checkout_revision(_FakeCtx(repo), 44, BROKER)

    assert _git(repo, "log", "--format=%s").splitlines() == [
        "D43: Do the thing in D43",
        "D42: Do the thing in D42",
        "base commit",
    ]
    # And each says what it is, so nobody mistakes it for the original commit.
    assert "Replayed by hackbot" in _git(repo, "log", "-1", "--format=%b", "HEAD")


async def test_ancestor_commits_keep_their_original_author(monkeypatch, tmp_path):
    # `git blame` over the revisions below the target should name whoever wrote
    # them, not hackbot. Hackbot stays the committer, which is the honest split.
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43)}, {42: [], 43: [42]}
    )
    _fake_conduit(
        monkeypatch,
        revisions,
        raw_diffs={
            42: _diff("base", "from D42"),
            43: _diff("from D42", "from D43"),
        },
    )

    await revision.checkout_revision(_FakeCtx(repo), 43, BROKER)

    assert _git(repo, "log", "-1", "--format=%an <%ae>", "HEAD").strip() == (
        "Author 42 <author42@example.com>"
    )
    assert _git(repo, "log", "-1", "--format=%cn", "HEAD").strip() == "Hackbot"


async def test_an_ancestor_without_author_info_still_commits(monkeypatch, tmp_path):
    # A diff uploaded through the web UI carries no commit to take an author
    # from. That must not stop the checkout; it just falls back to hackbot.
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43)}, {42: [], 43: [42]}
    )
    _fake_conduit(
        monkeypatch,
        revisions,
        with_authors=False,
        raw_diffs={
            42: _diff("base", "from D42"),
            43: _diff("from D42", "from D43"),
        },
    )

    await revision.checkout_revision(_FakeCtx(repo), 43, BROKER)

    assert _git(repo, "log", "-1", "--format=%s", "HEAD").strip() == (
        "D42: Do the thing in D42"
    )
    assert _git(repo, "log", "-1", "--format=%an", "HEAD").strip() == "Hackbot"


async def test_descendants_of_the_target_are_not_applied(monkeypatch, tmp_path):
    # D44 has a child D45. Only what is *below* the target is replayed.
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43), 44: _revision(44), 45: _revision(45)},
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


async def test_a_revision_at_the_bottom_of_someone_elses_stack(monkeypatch, tmp_path):
    # No parents but a child: "nothing below the target" is what matters, so
    # this takes the simple path and D43 is left alone.
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43)}, {42: [], 43: [42]}
    )
    _fake_conduit(
        monkeypatch,
        revisions,
        raw_diffs={
            42: _diff("base", "from D42"),
            43: _diff("from D42", "from D43"),
        },
    )

    await revision.checkout_revision(_FakeCtx(repo), 42, BROKER)

    assert (repo / "file.txt").read_text() == "from D42\n"
    assert _git(repo, "log", "--format=%s").splitlines() == ["base commit"]


async def test_a_stale_stack_fails_and_says_which_revision(monkeypatch, tmp_path):
    # A stack goes stale when a parent is updated and its children are not
    # rebased: D43's diff still expects D42's *old* content. Each revision's
    # latest diff is applied in order, so the context simply does not match.
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43)}, {42: [], 43: [42]}
    )
    _fake_conduit(
        monkeypatch,
        revisions,
        raw_diffs={
            42: _diff("base", "from D42 v2"),
            43: _diff("from D42 v1", "from D43"),
        },
    )

    with pytest.raises(RuntimeError) as failure:
        await revision.checkout_revision(_FakeCtx(repo), 43, BROKER)

    message = str(failure.value)
    assert "D43" in message
    assert "patch does not apply" in message
    assert "rebased" in message


async def test_a_failed_apply_leaves_the_checkout_untouched(monkeypatch, tmp_path):
    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph({42: _revision(42)}, {42: []})
    _fake_conduit(
        monkeypatch, revisions, raw_diffs={42: _diff("not what is there", "whatever")}
    )

    with pytest.raises(RuntimeError, match="D42"):
        await revision.checkout_revision(_FakeCtx(repo), 42, BROKER)

    # `git apply` is all-or-nothing.
    assert (repo / "file.txt").read_text() == "base\n"


async def test_try_push_from_a_stacked_checkout_carries_the_whole_stack(
    monkeypatch, tmp_path
):
    # After a stacked checkout the recorded source base is a local commit, so a
    # try push has to start from the commit that was actually fetched and carry
    # the ancestors in its patch series (Lando has no other way to get them).
    from hackbot_runtime import changes as changes_module

    repo = _repo_at_base(tmp_path)
    revisions = _with_stack_graph(
        {42: _revision(42), 43: _revision(43)}, {42: [], 43: [42]}
    )
    _fake_conduit(
        monkeypatch,
        revisions,
        raw_diffs={
            42: _diff("base", "from D42"),
            43: _diff("from D42", "from D43"),
        },
    )
    published = _git(repo, "rev-parse", "HEAD").strip()

    await revision.checkout_revision(_FakeCtx(repo), 43, BROKER)

    assert _git(repo, "rev-parse", "HEAD").strip() != published
    payload = changes_module.build_try_push(repo, published)
    assert payload["base_commit"] == published
    # The seeded ancestors commit plus the agent's work.
    assert len(payload["patches"]) == 2


def test_revision_needs_no_mozphab():
    # The checkout path talks to Conduit directly; moz-phab is only still a
    # dependency for building the diff that gets submitted back.
    source = Path(revision.__file__).read_text()
    assert "mozphab" not in source
