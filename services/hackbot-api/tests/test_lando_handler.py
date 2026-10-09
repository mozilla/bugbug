"""Tests for the apply-side Lando backout handler."""

import pytest
from app.action_handlers import ApplyContext, lando_handler
from app.action_handlers.registry import get_handler
from lando_client import LandoClient

_PARAMS = {"lando_repo": "firefox-autoland", "commit": "c" * 40, "reason": "x"}
_ATTACHMENTS = [{"name": "backout.patch", "uploaded_key": "attachments/a/backout"}]


@pytest.fixture(autouse=True)
def _lando_env(monkeypatch):
    monkeypatch.setenv("LANDO_ACCESS_TOKEN", "token")
    monkeypatch.setenv("LANDO_HEADLESS_API_TOKEN", "headless")
    lando_handler._client.cache_clear()
    yield
    lando_handler._client.cache_clear()


def _ctx(attachments=_ATTACHMENTS):
    async def download(key):
        assert key == "attachments/a/backout"
        return b"patch"

    return ApplyContext(
        run_id="run-1",
        agent="test-repair",
        download_artifact=download,
        attachments=attachments,
    )


@pytest.fixture
def pushed(monkeypatch):
    calls = []

    async def fake_push(self, repo_name, patches):
        calls.append((repo_name, patches))
        return 77

    monkeypatch.setattr(LandoClient, "push_commits", fake_push)
    return calls


def test_handler_is_registered():
    assert isinstance(get_handler("lando.backout"), lando_handler.BackoutHandler)


async def test_apply_pushes_the_recorded_patch(pushed):
    result = await lando_handler.BackoutHandler().apply(_PARAMS, _ctx())

    assert result.status == "applied"
    assert pushed == [("firefox-autoland", ["cGF0Y2g="])]
    assert result.result == {
        "job_id": 77,
        "url": "https://lando.moz.tools/api/job/77",
    }


async def test_apply_fails_without_a_patch(pushed):
    result = await lando_handler.BackoutHandler().apply(_PARAMS, _ctx(attachments=[]))

    assert result.status == "failed"
    assert pushed == []


async def test_apply_reports_a_lando_rejection(monkeypatch):
    async def fake_push(self, *args):
        raise RuntimeError("Lando returned HTTP 403: Missing permission")

    monkeypatch.setattr(LandoClient, "push_commits", fake_push)

    result = await lando_handler.BackoutHandler().apply(_PARAMS, _ctx())

    assert result.status == "failed"
    assert "Missing permission" in result.error
