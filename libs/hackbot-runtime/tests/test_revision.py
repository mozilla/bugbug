"""Tests for checking the source tree out at a Phabricator revision."""

import subprocess
from pathlib import Path

import httpx
import pytest
from hackbot_runtime import changes, revision

BROKER = "http://127.0.0.1:8765"

PARENT_DIFF = """diff --git a/f.txt b/f.txt
--- a/f.txt
+++ b/f.txt
@@ -1 +1,2 @@
 one
+two
"""

CHILD_DIFF = """diff --git a/f.txt b/f.txt
--- a/f.txt
+++ b/f.txt
@@ -1,2 +1,3 @@
 one
 two
+three
"""


def _patch(
    revision_id: int, diff_id: int, raw_diff: str, base_commit: str = "recorded-base"
) -> dict:
    return {
        "revision_id": revision_id,
        "diff_id": diff_id,
        "base_commit": base_commit,
        "raw_diff": raw_diff,
    }


class _FakeCtx:
    """Stand-in for HackbotContext: records the ref passed to prepare_repo."""

    def __init__(self, repo: Path, track_head: bool = False):
        self._repo = repo
        self._track_head = track_head
        self.prepared_ref = None
        self.source_base = None
        self.source_base_resets = 0
        self.reported_base = None

    async def prepare_repo(
        self, ref: str | None = None, depth: int | None = None
    ) -> Path:
        self.prepared_ref = ref
        if self._track_head:
            self.source_base = changes.base_commit(self._repo)
        return self._repo

    def reset_source_base(self, reported_base: str | None = None) -> None:
        self.source_base_resets += 1
        self.reported_base = reported_base
        if self._track_head:
            self.source_base = changes.base_commit(self._repo)


def _patch_broker(monkeypatch, *, status=200, payload=None, text=""):
    """Stub httpx.AsyncClient.get to return a canned broker response."""
    captured = {}

    class _Resp:
        status_code = status

        def json(self):
            return payload

    _Resp.text = text

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            captured["url"] = url
            return _Resp()

    monkeypatch.setattr(revision.httpx, "AsyncClient", _FakeAsyncClient)
    return captured


def _patch_git(monkeypatch, *, returncode=0, stderr=b""):
    calls = []

    def _fake_run(cmd, input=None, capture_output=False):
        calls.append({"cmd": cmd, "input": input})
        return type("R", (), {"returncode": returncode, "stderr": stderr})()

    monkeypatch.setattr(revision.subprocess, "run", _fake_run)
    return calls


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A one-commit git repo standing in for the prepared checkout."""
    repo = tmp_path / "src"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f.txt").write_text("one\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-m",
            "base",
        ],
        check=True,
        capture_output=True,
    )
    return repo


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


async def test_checkout_applies_diff_at_base(monkeypatch, tmp_path):
    http = _patch_broker(
        monkeypatch,
        payload={
            "base_commit": "base9",
            "patches": [_patch(42, 9, "diff --git a/f b/f\n")],
        },
    )
    git = _patch_git(monkeypatch)
    ctx = _FakeCtx(tmp_path)

    await revision.checkout_revision(ctx, 42, BROKER)

    assert http["url"] == f"{BROKER}/phabricator/revision/42/patch"
    assert ctx.prepared_ref == "base9"
    assert len(git) == 1
    assert git[0]["cmd"][:4] == ["git", "-C", str(tmp_path), "apply"]
    assert git[0]["input"] == b"diff --git a/f b/f\n"
    # Nothing was committed, so the change base stays at the revision's base.
    assert ctx.source_base_resets == 0


async def test_checkout_raises_on_broker_error(monkeypatch, tmp_path):
    _patch_broker(monkeypatch, status=404, text='{"error": "no diffs"}')
    ctx = _FakeCtx(tmp_path)
    with pytest.raises(RuntimeError, match="Broker could not provide patch for D42"):
        await revision.checkout_revision(ctx, 42, BROKER)


async def test_checkout_raises_when_the_broker_returns_no_patches(
    monkeypatch, tmp_path
):
    _patch_broker(monkeypatch, payload={"base_commit": "base9", "patches": []})
    ctx = _FakeCtx(tmp_path)
    with pytest.raises(RuntimeError, match="no patches for D42"):
        await revision.checkout_revision(ctx, 42, BROKER)


async def test_checkout_raises_when_apply_fails(monkeypatch, tmp_path):
    _patch_broker(
        monkeypatch,
        payload={
            "base_commit": "base9",
            "patches": [_patch(42, 9, "diff --git a/f b/f\n")],
        },
    )
    _patch_git(monkeypatch, returncode=1, stderr=b"patch does not apply")
    ctx = _FakeCtx(tmp_path)
    with pytest.raises(RuntimeError, match="Could not apply diff for D42"):
        await revision.checkout_revision(ctx, 42, BROKER)


async def test_checkout_names_the_stacked_revision_that_fails_to_apply(
    monkeypatch, tmp_path
):
    # The failing patch is the parent's, so the error must not blame D42.
    _patch_broker(
        monkeypatch,
        payload={
            "base_commit": "base9",
            "patches": [_patch(41, 8, "parent\n"), _patch(42, 9, "child\n")],
        },
    )
    _patch_git(monkeypatch, returncode=1, stderr=b"patch does not apply")
    ctx = _FakeCtx(tmp_path)
    with pytest.raises(RuntimeError, match="Could not apply diff for D41"):
        await revision.checkout_revision(ctx, 42, BROKER)


async def test_checkout_rebuilds_a_stack_onto_the_fetchable_base(monkeypatch, repo):
    # D42 is stacked on the unlanded D41: both diffs are replayed, the parent's
    # as a commit and D42's own left in the working tree.
    _patch_broker(
        monkeypatch,
        payload={
            "base_commit": "base9",
            "patches": [
                _patch(41, 8, PARENT_DIFF, base_commit="landed-base"),
                _patch(42, 9, CHILD_DIFF, base_commit="69706d7a081e"),
            ],
        },
    )
    ctx = _FakeCtx(repo, track_head=True)
    base = _git_out(repo, "rev-parse", "HEAD")

    await revision.checkout_revision(ctx, 42, BROKER)

    # The tree is the revision's: base + parent + child.
    assert (repo / "f.txt").read_text() == "one\ntwo\nthree\n"
    # The parent is history the run inherits, so the change base moved onto it
    # and only D42's own diff is left uncommitted for the agent to build on.
    assert ctx.source_base_resets == 1
    assert ctx.source_base == _git_out(repo, "rev-parse", "HEAD") != base
    # An updated diff still declares the base D42 recorded: the commit the tree
    # was rebuilt at is local to this container, and nothing was rebased.
    assert ctx.reported_base == "69706d7a081e"
    assert _git_out(repo, "show", "HEAD:f.txt") == "one\ntwo"
    assert _git_out(repo, "log", "-1", "--format=%s") == (
        "D41 diff 8 (unlanded parent of D42)"
    )
    # Unstaged, i.e. D42's diff is the run's starting point, not yet a commit.
    assert _git_out(repo, "status", "--porcelain") == "M f.txt"


async def test_checkout_leaves_an_unstacked_revision_uncommitted(monkeypatch, repo):
    _patch_broker(
        monkeypatch,
        payload={"base_commit": "base9", "patches": [_patch(42, 9, PARENT_DIFF)]},
    )
    ctx = _FakeCtx(repo, track_head=True)
    base = _git_out(repo, "rev-parse", "HEAD")

    await revision.checkout_revision(ctx, 42, BROKER)

    assert (repo / "f.txt").read_text() == "one\ntwo\n"
    assert _git_out(repo, "rev-parse", "HEAD") == base
    assert ctx.source_base_resets == 0


def test_revision_uses_httpx():
    # Guard against reintroducing an in-agent Conduit client (which needs a key).
    assert revision.httpx is httpx
