"""Tests for the apply-side Lando backout handler."""

import pytest
from app.action_handlers import ApplyContext, lando_handler
from app.action_handlers.registry import get_handler
from lando_client import LandoClient

_PARAMS = {"lando_repo": "firefox-autoland", "commit": "c" * 40}


@pytest.fixture(autouse=True)
def _lando_env(monkeypatch):
    monkeypatch.setenv("LANDO_ACCESS_TOKEN", "token")
    monkeypatch.setenv("LANDO_HEADLESS_API_TOKEN", "headless")
    lando_handler._client.cache_clear()
    yield
    lando_handler._client.cache_clear()


def _ctx():
    async def download(key):
        raise AssertionError("a backout reads no artifact")

    return ApplyContext(
        run_id="run-1",
        agent="test-repair",
        download_artifact=download,
        hackbot_client=object(),
    )


def test_handler_is_registered():
    assert isinstance(get_handler("lando.backout"), lando_handler.BackoutHandler)


async def test_apply_reverts_the_recorded_commit(monkeypatch):
    calls = []

    async def fake_revert(self, repo_name, commit):
        calls.append((repo_name, commit))
        return 77

    monkeypatch.setattr(LandoClient, "revert_commit", fake_revert)

    result = await lando_handler.BackoutHandler().apply(_PARAMS, _ctx())

    assert result.status == "applied"
    assert calls == [("firefox-autoland", "c" * 40)]
    assert result.result == {
        "job_id": 77,
        "url": "https://lando.moz.tools/api/job/77",
    }


async def test_apply_reports_a_lando_rejection(monkeypatch):
    async def fake_revert(self, *args):
        raise RuntimeError("Lando returned HTTP 403: not allowed to revert commits")

    monkeypatch.setattr(LandoClient, "revert_commit", fake_revert)

    result = await lando_handler.BackoutHandler().apply(_PARAMS, _ctx())

    assert result.status == "failed"
    assert "not allowed to revert commits" in result.error
