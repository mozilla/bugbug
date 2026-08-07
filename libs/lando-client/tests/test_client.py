"""Tests for the shared Lando client."""

import re
from base64 import b64decode

import httpx
import pytest
from lando_client import (
    LandoAPIError,
    LandoClient,
    LandoSettings,
    encode_patch,
)
from lando_client import client as client_module
from pydantic import ValidationError


def _client(**kwargs) -> LandoClient:
    return LandoClient(LandoSettings(access_token="token", **kwargs))


def _capture_post(monkeypatch, response: httpx.Response) -> dict:
    """Stub httpx.AsyncClient to answer with `response`; capture the call."""
    captured: dict = {}

    class _FakeAsyncClient:
        def __init__(self, timeout=None):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            response.request = httpx.Request("POST", url)
            return response

    monkeypatch.setattr(client_module.httpx, "AsyncClient", _FakeAsyncClient)
    return captured


def test_access_token_is_required():
    with pytest.raises(ValidationError):
        LandoSettings(access_token="")


def test_urls_are_built_from_the_configured_host():
    client = _client(url="https://lando-dev.allizom.org/")

    # A trailing slash in config must not double up in the URLs.
    assert client.try_patches_url == "https://lando-dev.allizom.org/api/try/patches"
    assert client.job_url(7) == "https://lando-dev.allizom.org/landings/7"
    # Pointing at another deployment moves the Treeherder link with it, with no
    # second setting to keep in step.
    assert client.treeherder_url(7) == (
        "https://treeherder.mozilla.org/jobs?repo=try"
        "&landoInstance=lando-dev-2025&landoCommitID=7"
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://lando.moz.tools", "lando-prod-2025"),
        ("https://lando-dev.allizom.org", "lando-dev-2025"),
        ("https://api.lando.services.mozilla.com", "lando-prod"),
        ("https://api.dev.lando.nonprod.cloudops.mozgcp.net", "lando-dev"),
    ],
)
def test_instance_id_is_derived_from_the_host(url, expected):
    """Matches Treeherder's own instance -> host map (ui/helpers/url.js)."""
    assert _client(url=url).settings.instance_id == expected


def test_instance_id_defaults_to_prod_without_any_configuration():
    assert LandoSettings(access_token="t").instance_id == "lando-prod-2025"


def test_explicit_instance_id_wins_for_an_unknown_host():
    settings = LandoSettings(
        access_token="t", url="https://lando.example.test", instance_id="lando-local"
    )
    assert settings.instance_id == "lando-local"


def test_unknown_host_without_an_instance_id_is_rejected():
    """A wrong id points Treeherder at the wrong Lando, so never guess one."""
    with pytest.raises(ValidationError, match="Unknown Lando host"):
        LandoSettings(access_token="t", url="https://lando.example.test")


def test_treeherder_url_follows_the_repo():
    assert "repo=try-comm-central" in _client().treeherder_url(7, "try-comm-central")


def test_encode_patch_is_plain_base64():
    # Lando's schema rejects anything but `^[A-Za-z0-9+/]+={0,2}$`, so a long
    # patch must not come back wrapped across lines.
    patch = b"From abc\nSubject: [PATCH] a fix\n" * 50
    encoded = encode_patch(patch)

    assert re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", encoded)
    assert b64decode(encoded) == patch


async def test_submit_try_patches_posts_the_series_and_returns_the_job_id(monkeypatch):
    captured = _capture_post(monkeypatch, httpx.Response(201, json={"id": 4321}))

    job_id = await _client().submit_try_patches(["cGF0Y2g="], "a" * 40)

    assert job_id == 4321
    assert captured["url"] == "https://lando.moz.tools/api/try/patches"
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["json"] == {
        "repo_name": "try",
        "base_commit": "a" * 40,
        "base_commit_vcs": "git",
        "patch_format": "git-format-patch",
        "patches": ["cGF0Y2g="],
    }


async def test_submit_try_patches_raises_with_lando_problem_detail(monkeypatch):
    _capture_post(
        monkeypatch,
        httpx.Response(
            400,
            json={
                "title": "Not a Try repository",
                "detail": "Repo autoland is not a Try repository.",
                "status": 400,
                "type": "about:blank",
            },
        ),
    )

    with pytest.raises(LandoAPIError) as excinfo:
        await _client().submit_try_patches(["cGF0Y2g="], "a" * 40)

    assert "Not a Try repository" in str(excinfo.value)
    assert "Repo autoland is not a Try repository." in str(excinfo.value)


async def test_submit_try_patches_raises_on_a_non_json_error(monkeypatch):
    """A 5xx from a proxy answers with HTML, not a problem detail."""
    _capture_post(
        monkeypatch, httpx.Response(503, text="<html>Service Unavailable</html>")
    )

    with pytest.raises(LandoAPIError) as excinfo:
        await _client().submit_try_patches(["cGF0Y2g="], "a" * 40)

    assert "HTTP 503" in str(excinfo.value)


async def test_submit_try_patches_raises_when_no_job_id_comes_back(monkeypatch):
    _capture_post(monkeypatch, httpx.Response(201, json={}))

    with pytest.raises(LandoAPIError) as excinfo:
        await _client().submit_try_patches(["cGF0Y2g="], "a" * 40)

    assert "no job id" in str(excinfo.value)
