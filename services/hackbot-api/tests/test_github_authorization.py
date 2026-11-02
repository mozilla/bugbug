"""Tests for GitHub webhook sender authorization."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.config import GitHubAppSettings, Settings
from app.github import GitHubApp
from app.github_authorization import (
    DEFAULT_LOOKUP_TIMEOUT_SECONDS,
    GitHubAuthorizer,
    is_bot_sender,
)
from app.routers.webhooks import get_github_authorizer
from cachetools import TTLCache
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

APP_ID = 123
INSTALLATION_ID = 456
OTHER_INSTALLATION_ID = 789
ALLOWED_ROLES = GitHubAppSettings.model_fields["allowed_roles"].default
BOT_LOGIN = GitHubAppSettings.model_fields["bot_login"].default


def _user(login: str) -> dict[str, str]:
    """A webhook ``sender`` for a regular user account."""
    return {"login": login, "type": "User"}


@pytest.fixture(scope="module")
def pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


class _FakeGitHub:
    """Stands in for api.github.com: mints tokens and answers permission checks.

    ``roles`` maps a lowercased login to its ``role_name``; a login missing from
    it gets a 404, like a user who isn't a collaborator. ``status`` forces every
    permission check to fail with that status instead. ``mint_delay`` and
    ``permission_delay`` slow those responses down by that many seconds.
    """

    def __init__(
        self,
        roles: dict[str, str],
        status: int | None = None,
        *,
        mint_delay: float = 0,
        permission_delay: float = 0,
    ):
        self.roles = roles
        self.status = status
        self.mint_delay = mint_delay
        self.permission_delay = permission_delay
        self.requests: list[httpx.Request] = []

    @property
    def mint_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/access_tokens")]

    @property
    def permission_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/permission")]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/access_tokens"):
            await asyncio.sleep(self.mint_delay)
            expires_at = datetime.now(UTC) + timedelta(hours=1)
            return httpx.Response(
                201,
                json={
                    "token": "ghs_test",
                    "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        if path.endswith("/permission"):
            await asyncio.sleep(self.permission_delay)
            if self.status is not None:
                return httpx.Response(self.status, json={"message": "nope"})
            login = path.split("/")[-2]
            role = self.roles.get(login.lower())
            if role is None:
                return httpx.Response(404, json={"message": "Not Found"})
            # The legacy field folds maintain into write and triage into read,
            # so a check reading it instead of role_name would get these wrong.
            legacy = {"maintain": "write", "triage": "read"}.get(role, role)
            return httpx.Response(
                200,
                json={"permission": legacy, "role_name": role, "user": None},
            )
        return httpx.Response(500, json={"message": f"unexpected {path}"})


def _authorizer(
    pem: str, fake: _FakeGitHub, **kwargs
) -> tuple[GitHubAuthorizer, GitHubApp]:
    github_app = GitHubApp(
        GitHubAppSettings(app_id=APP_ID, private_key=pem),
        async_transport=httpx.MockTransport(fake),
        http_cache=False,
        auto_retry=False,
    )
    return GitHubAuthorizer(github_app, ALLOWED_ROLES, BOT_LOGIN, **kwargs), github_app


# --- roles ---


@pytest.mark.parametrize("role", ["admin", "maintain", "write"])
async def test_allowed_roles_are_authorized(pem, role):
    authorizer, _ = _authorizer(pem, _FakeGitHub({"dev": role}))
    assert await authorizer.is_authorized(
        INSTALLATION_ID, "mozilla", "repo", _user("dev")
    )


@pytest.mark.parametrize("role", ["triage", "read", "none", "security-reviewer"])
async def test_other_roles_are_denied(pem, role):
    authorizer, _ = _authorizer(pem, _FakeGitHub({"dev": role}))
    assert not await authorizer.is_authorized(
        INSTALLATION_ID, "mozilla", "repo", _user("dev")
    )


async def test_configured_roles_replace_the_defaults(pem):
    # The legacy permission field reports maintain as write, so only role_name
    # can tell a maintainer from a writer.
    fake = _FakeGitHub({"maintainer": "maintain", "writer": "write"})
    github_app = GitHubApp(
        GitHubAppSettings(app_id=APP_ID, private_key=pem),
        async_transport=httpx.MockTransport(fake),
        http_cache=False,
        auto_retry=False,
    )
    authorizer = GitHubAuthorizer(github_app, ["maintain"], BOT_LOGIN)

    assert await authorizer.is_authorized(
        INSTALLATION_ID, "o", "r", _user("maintainer")
    )
    assert not await authorizer.is_authorized(
        INSTALLATION_ID, "o", "r", _user("writer")
    )


# --- failures fail closed ---


async def test_non_collaborator_is_denied(pem):
    fake = _FakeGitHub({})
    authorizer, _ = _authorizer(pem, fake)

    assert not await authorizer.is_authorized(
        INSTALLATION_ID, "mozilla", "repo", _user("stranger")
    )
    assert len(fake.permission_requests) == 1


@pytest.mark.parametrize("status", [401, 403, 500, 502])
async def test_error_responses_are_denied_and_not_cached(pem, status):
    fake = _FakeGitHub({"dev": "admin"}, status=status)
    authorizer, _ = _authorizer(pem, fake)

    assert not await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))

    # Once GitHub recovers, the user isn't stuck with the failed answer.
    fake.status = None
    assert await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))


async def test_network_error_is_denied(pem):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    github_app = GitHubApp(
        GitHubAppSettings(app_id=APP_ID, private_key=pem),
        async_transport=httpx.MockTransport(handler),
        http_cache=False,
        auto_retry=False,
    )
    authorizer = GitHubAuthorizer(github_app, ALLOWED_ROLES, BOT_LOGIN)

    assert not await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))


async def test_token_mint_failure_is_denied(pem):
    # E.g. the installation doesn't cover the repository.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "no access"})

    github_app = GitHubApp(
        GitHubAppSettings(app_id=APP_ID, private_key=pem),
        async_transport=httpx.MockTransport(handler),
        http_cache=False,
        auto_retry=False,
    )
    authorizer = GitHubAuthorizer(github_app, ALLOWED_ROLES, BOT_LOGIN)

    assert not await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))


async def test_slow_lookup_is_denied_and_not_cached(pem):
    fake = _FakeGitHub({"dev": "admin"}, permission_delay=5)
    authorizer, _ = _authorizer(pem, fake, lookup_timeout_seconds=0.1)

    started = time.monotonic()
    assert not await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))
    # Gave up at the deadline rather than waiting for GitHub.
    assert time.monotonic() - started < 2

    # Once GitHub is fast again, the user isn't stuck with the timed-out answer.
    fake.permission_delay = 0
    assert await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))


async def test_deadline_covers_token_mint_and_lookup_together(pem):
    # Each request fits the deadline alone; a cold check makes both in turn.
    fake = _FakeGitHub({"dev": "admin"}, mint_delay=0.3, permission_delay=0.3)
    authorizer, _ = _authorizer(pem, fake, lookup_timeout_seconds=0.5)

    assert not await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))
    assert len(fake.mint_requests) == 1


@pytest.mark.parametrize(
    "sender",
    [
        {"login": "hackbot[bot]", "type": "Bot"},
        _user("hackbot"),
        _user("ghost"),
        None,
    ],
)
async def test_bots_and_deleted_users_are_denied_without_a_lookup(pem, sender):
    fake = _FakeGitHub({"hackbot[bot]": "admin", "hackbot": "admin", "ghost": "admin"})
    authorizer, _ = _authorizer(pem, fake)

    assert not await authorizer.is_authorized(INSTALLATION_ID, "o", "r", sender)
    assert fake.requests == []


# --- installation and token ---


async def test_uses_the_events_installation_with_a_narrow_token(pem):
    fake = _FakeGitHub({"dev": "write"})
    authorizer, _ = _authorizer(pem, fake)

    assert await authorizer.is_authorized(
        OTHER_INSTALLATION_ID, "mozilla-mobile", "firefox-ios", _user("dev")
    )

    (mint,) = fake.mint_requests
    assert mint.url.path == f"/app/installations/{OTHER_INSTALLATION_ID}/access_tokens"
    assert json.loads(mint.content) == {
        "permissions": {"metadata": "read"},
        "repositories": ["firefox-ios"],
    }
    (check,) = fake.permission_requests
    assert check.url.path == (
        "/repos/mozilla-mobile/firefox-ios/collaborators/dev/permission"
    )
    assert check.headers["Authorization"] == "token ghs_test"


async def test_ignores_the_default_installation(pem):
    fake = _FakeGitHub({"dev": "write"})
    github_app = GitHubApp(
        GitHubAppSettings(
            app_id=APP_ID, private_key=pem, installation_id=INSTALLATION_ID
        ),
        async_transport=httpx.MockTransport(fake),
        http_cache=False,
        auto_retry=False,
    )
    authorizer = GitHubAuthorizer(github_app, ALLOWED_ROLES, BOT_LOGIN)

    assert await authorizer.is_authorized(OTHER_INSTALLATION_ID, "o", "r", _user("dev"))
    (mint,) = fake.mint_requests
    assert str(OTHER_INSTALLATION_ID) in mint.url.path


# --- caching ---


async def test_caches_positive_and_negative_answers(pem):
    fake = _FakeGitHub({"dev": "write", "reader": "read"})
    authorizer, _ = _authorizer(pem, fake)

    for _ in range(2):
        assert await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))
        assert not await authorizer.is_authorized(
            INSTALLATION_ID, "o", "r", _user("reader")
        )
        assert not await authorizer.is_authorized(
            INSTALLATION_ID, "o", "r", _user("stranger")
        )

    assert len(fake.permission_requests) == 3


async def test_cache_is_per_repository_and_installation(pem):
    fake = _FakeGitHub({"dev": "write"})
    authorizer, _ = _authorizer(pem, fake)

    await authorizer.is_authorized(INSTALLATION_ID, "o", "r1", _user("dev"))
    await authorizer.is_authorized(INSTALLATION_ID, "o", "r2", _user("dev"))
    await authorizer.is_authorized(INSTALLATION_ID, "other", "r1", _user("dev"))
    await authorizer.is_authorized(OTHER_INSTALLATION_ID, "o", "r1", _user("dev"))

    assert len(fake.permission_requests) == 4


async def test_cache_expires(pem):
    fake = _FakeGitHub({"dev": "write"})
    authorizer, _ = _authorizer(pem, fake)
    now = [0.0]
    authorizer._cache = TTLCache(maxsize=16, ttl=300, timer=lambda: now[0])

    assert await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))
    # Write access is revoked; the cached answer holds until it expires.
    fake.roles["dev"] = "read"
    now[0] = 299
    assert await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))
    now[0] = 301
    assert not await authorizer.is_authorized(INSTALLATION_ID, "o", "r", _user("dev"))
    assert len(fake.permission_requests) == 2


# --- bot filter ---


@pytest.mark.parametrize(
    "sender",
    [
        {"login": "dependabot[bot]", "type": "Bot"},
        {"login": "some-app", "type": "Bot"},
        {"login": "renovate[bot]", "type": "User"},
        {"login": "hackbot[bot]", "type": "Bot"},
        # Hackbot's own account, which carries no [bot] suffix.
        {"login": "hackbot", "type": "User"},
        {},
        None,
    ],
)
def test_is_bot_sender_rejects_bots(sender):
    assert is_bot_sender(sender, BOT_LOGIN)


@pytest.mark.parametrize(
    "sender",
    [
        {"login": "octocat", "type": "User"},
        {"login": "bot-builder", "type": "User"},
        {"login": "hackbot-fan", "type": "User"},
    ],
)
def test_is_bot_sender_accepts_users(sender):
    assert not is_bot_sender(sender, BOT_LOGIN)


# --- settings ---


def test_trigger_settings_defaults(pem):
    github = GitHubAppSettings(app_id=APP_ID, private_key=pem)
    assert github.bot_login == "hackbot"
    assert github.mention_token == "@hackbot"
    assert github.allowed_roles == {"admin", "maintain", "write"}


def test_trigger_settings_strip_roles(pem):
    github = GitHubAppSettings(
        app_id=APP_ID, private_key=pem, allowed_roles=[" admin ", "write"]
    )
    assert github.allowed_roles == {"admin", "write"}


@pytest.mark.parametrize(
    ("roles", "error"),
    [
        ([], "at least 1 item"),
        ([""], "at least 1 character"),
        (["  "], "at least 1 character"),
    ],
)
def test_trigger_settings_reject_empty_roles(pem, roles, error):
    with pytest.raises(ValidationError, match=error):
        GitHubAppSettings(app_id=APP_ID, private_key=pem, allowed_roles=roles)


def _clear_github_env(monkeypatch):
    for name in (
        "GITHUB_APP_ID",
        "GITHUB_PRIVATE_KEY",
        "GITHUB_INSTALLATION_ID",
        "GITHUB_BOT_LOGIN",
        "GITHUB_MENTION_TOKEN",
        "GITHUB_ALLOWED_ROLES",
        "GITHUB_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_settings_start_without_github_config(monkeypatch):
    _clear_github_env(monkeypatch)
    assert Settings(_env_file=None).github is None


def test_settings_read_trigger_env(monkeypatch, pem):
    _clear_github_env(monkeypatch)
    monkeypatch.setenv("GITHUB_APP_ID", str(APP_ID))
    monkeypatch.setenv("GITHUB_PRIVATE_KEY", pem)
    monkeypatch.setenv("GITHUB_BOT_LOGIN", "other[bot]")
    monkeypatch.setenv("GITHUB_MENTION_TOKEN", "@other")
    monkeypatch.setenv("GITHUB_ALLOWED_ROLES", '["admin"]')

    loaded = Settings(_env_file=None)

    assert loaded.github.app_id == APP_ID
    assert loaded.github.bot_login == "other[bot]"
    assert loaded.github.mention_token == "@other"
    assert loaded.github.allowed_roles == {"admin"}


# --- dependency ---


class _Request:
    def __init__(self):
        self.app = type("App", (), {"state": type("State", (), {})()})()


def test_dependency_requires_github_app(monkeypatch):
    from app.routers import webhooks
    from fastapi import HTTPException

    monkeypatch.setattr(webhooks.settings, "github", None)
    with pytest.raises(HTTPException) as excinfo:
        get_github_authorizer(_Request())
    assert excinfo.value.status_code == 503


def test_dependency_is_app_scoped(monkeypatch, pem):
    from app.routers import webhooks

    monkeypatch.setattr(
        webhooks.settings,
        "github",
        GitHubAppSettings(app_id=APP_ID, private_key=pem),
    )
    request = _Request()

    first = get_github_authorizer(request)
    assert isinstance(first, GitHubAuthorizer)
    assert get_github_authorizer(request) is first
    # Fails within GitHub's 10-second webhook timeout instead of retrying 5xx
    # or waiting on a slow GitHub.
    assert first._github_app._github_options["auto_retry"] is False
    assert first._lookup_timeout_seconds == DEFAULT_LOOKUP_TIMEOUT_SECONDS < 10
    assert first._bot_login == "hackbot"
