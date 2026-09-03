"""Tests for checking the source tree out at a Phabricator revision."""

from pathlib import Path

import httpx
import pytest
from hackbot_runtime import revision

BROKER = "http://127.0.0.1:8765"


class _FakeCtx:
    """Stand-in for HackbotContext: records the ref passed to prepare_repo."""

    def __init__(self, repo: Path):
        self._repo = repo
        self.prepared_ref = None

    async def prepare_repo(
        self, ref: str | None = None, depth: int | None = None
    ) -> Path:
        self.prepared_ref = ref
        return self._repo


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
    calls = {}

    def _fake_run(cmd, input=None, capture_output=False):
        calls["cmd"] = cmd
        calls["input"] = input
        return type("R", (), {"returncode": returncode, "stderr": stderr})()

    monkeypatch.setattr(revision.subprocess, "run", _fake_run)
    return calls


async def test_checkout_applies_diff_at_base(monkeypatch, tmp_path):
    http = _patch_broker(
        monkeypatch,
        payload={"base_commit": "base9", "raw_diff": "diff --git a/f b/f\n"},
    )
    git = _patch_git(monkeypatch)
    ctx = _FakeCtx(tmp_path)

    await revision.checkout_revision(ctx, 42, BROKER)

    assert http["url"] == f"{BROKER}/phabricator/revision/42/patch"
    assert ctx.prepared_ref == "base9"
    assert git["cmd"][:4] == ["git", "-C", str(tmp_path), "apply"]
    assert git["input"] == b"diff --git a/f b/f\n"


async def test_checkout_raises_on_broker_error(monkeypatch, tmp_path):
    _patch_broker(monkeypatch, status=404, text='{"error": "no diffs"}')
    ctx = _FakeCtx(tmp_path)
    with pytest.raises(RuntimeError, match="Broker could not provide patch for D42"):
        await revision.checkout_revision(ctx, 42, BROKER)


async def test_checkout_raises_when_apply_fails(monkeypatch, tmp_path):
    _patch_broker(
        monkeypatch,
        payload={"base_commit": "base9", "raw_diff": "diff --git a/f b/f\n"},
    )
    _patch_git(monkeypatch, returncode=1, stderr=b"patch does not apply")
    ctx = _FakeCtx(tmp_path)
    with pytest.raises(RuntimeError, match="Could not apply diff for D42"):
        await revision.checkout_revision(ctx, 42, BROKER)


def test_revision_uses_httpx():
    # Guard against reintroducing an in-agent Conduit client (which needs a key).
    assert revision.httpx is httpx
