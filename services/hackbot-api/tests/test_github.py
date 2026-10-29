"""Tests for GitHub App authentication and API configuration."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from app.config import GitHubAppSettings, Settings
from app.github import (
    READ_ONLY_PERMISSIONS,
    READ_WRITE_PERMISSIONS,
    GitHubApp,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from pydantic import ValidationError

APP_ID = 123
INSTALLATION_ID = 456


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def pem(rsa_key) -> str:
    return rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


class _FakeGitHub:
    """Stands in for api.github.com, minting a numbered token per request."""

    def __init__(self, expires_in: timedelta = timedelta(hours=1)):
        self.expires_in = expires_in
        self.requests: list[httpx.Request] = []

    @property
    def mint_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/access_tokens")]

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/access_tokens"):
            # Yield like a real request would, so concurrent callers interleave.
            await asyncio.sleep(0)
            expires_at = datetime.now(UTC) + self.expires_in
            return httpx.Response(
                201,
                json={
                    "token": f"ghs_{len(self.mint_requests)}",
                    "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
            )
        return httpx.Response(200, json={})


def _app(pem: str, fake: _FakeGitHub, **settings) -> GitHubApp:
    settings = {"app_id": APP_ID, "private_key": pem, **settings}
    return GitHubApp(
        GitHubAppSettings(**settings),
        async_transport=httpx.MockTransport(fake),
        http_cache=False,
    )


def _set_env(monkeypatch, **env: str) -> None:
    for name in ("GITHUB_APP_ID", "GITHUB_PRIVATE_KEY", "GITHUB_INSTALLATION_ID"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(f"GITHUB_{name}", value)


def test_github_is_optional_until_credentials_are_deployed(monkeypatch):
    _set_env(monkeypatch)
    assert Settings(_env_file=None).github is None


def test_settings_errors_do_not_expose_the_private_key(monkeypatch, pem):
    _set_env(monkeypatch, APP_ID="invalid", PRIVATE_KEY=pem)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    assert "PRIVATE KEY" not in str(exc.value)
    assert "input_value" not in str(exc.value)


def test_settings_load_from_env(monkeypatch, pem):
    _set_env(monkeypatch, APP_ID="123", PRIVATE_KEY=pem, INSTALLATION_ID="456")
    settings = Settings(_env_file=None).github
    assert settings is not None
    assert settings.app_id == 123
    assert settings.installation_id == 456
    assert settings.private_key.get_secret_value() == pem.strip()


def test_settings_accept_a_private_key_with_escaped_newlines(monkeypatch, pem):
    _set_env(monkeypatch, APP_ID="123", PRIVATE_KEY=pem.replace("\n", "\\n"))
    settings = Settings(_env_file=None).github
    assert settings is not None
    assert settings.private_key.get_secret_value() == pem.strip()


@pytest.mark.parametrize("missing", ["APP_ID", "PRIVATE_KEY"])
def test_settings_require_app_id_and_private_key(monkeypatch, pem, missing):
    env = {"APP_ID": "123", "PRIVATE_KEY": pem}
    del env[missing]
    _set_env(monkeypatch, **env)
    with pytest.raises(ValidationError, match=missing.lower()):
        Settings(_env_file=None)


@pytest.mark.parametrize("app_id", ["abc", "0", "-1"])
def test_settings_reject_an_invalid_app_id(monkeypatch, pem, app_id):
    _set_env(monkeypatch, APP_ID=app_id, PRIVATE_KEY=pem)
    with pytest.raises(ValidationError, match="app_id"):
        Settings(_env_file=None)


def _ec_pem() -> str:
    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


@pytest.mark.parametrize("private_key", ["not-a-key", "", _ec_pem()])
def test_settings_reject_anything_but_an_rsa_private_key(monkeypatch, private_key):
    _set_env(monkeypatch, APP_ID="123", PRIVATE_KEY=private_key)
    with pytest.raises(ValidationError, match="PEM-encoded RSA private key") as exc:
        Settings(_env_file=None)
    assert "input_value" not in str(exc.value)


def test_settings_repr_hides_the_private_key(pem):
    settings = GitHubAppSettings(app_id=APP_ID, private_key=pem)
    assert "PRIVATE KEY" not in repr(settings)


async def test_app_client_signs_a_short_lived_jwt(pem, rsa_key):
    fake = _FakeGitHub()
    await _app(pem, fake).as_app().arequest("GET", "/app")

    scheme, token = fake.requests[0].headers["Authorization"].split()
    assert scheme == "Bearer"
    # Also verifies the signature and that the token hasn't expired.
    claims = jwt.decode(token, rsa_key.public_key(), algorithms=["RS256"])
    assert claims["iss"] == str(APP_ID)
    assert claims["exp"] - claims["iat"] <= 10 * 60


@pytest.mark.parametrize(
    "permissions", [READ_ONLY_PERMISSIONS, READ_WRITE_PERMISSIONS], ids=["ro", "rw"]
)
async def test_installation_token_requests_the_given_permissions(
    pem, rsa_key, permissions
):
    fake = _FakeGitHub()
    token = await _app(pem, fake).installation_token(
        INSTALLATION_ID, permissions=permissions
    )

    assert token.token == "ghs_1"
    assert token.expires_at > datetime.now(UTC)
    [request] = fake.requests
    assert request.method == "POST"
    assert request.url.path == f"/app/installations/{INSTALLATION_ID}/access_tokens"
    assert json.loads(request.content) == {"permissions": permissions}
    _, jwt_token = request.headers["Authorization"].split()
    assert jwt.decode(jwt_token, rsa_key.public_key(), algorithms=["RS256"])


def test_permission_presets():
    assert set(READ_ONLY_PERMISSIONS.values()) == {"read"}
    assert set(READ_WRITE_PERMISSIONS.values()) == {"write"}
    assert READ_ONLY_PERMISSIONS.keys() == READ_WRITE_PERMISSIONS.keys()


@pytest.mark.parametrize("preset", [READ_ONLY_PERMISSIONS, READ_WRITE_PERMISSIONS])
def test_permission_presets_cannot_be_widened(preset):
    with pytest.raises(TypeError):
        preset["administration"] = "write"


async def test_installation_token_can_be_limited_to_repositories(pem):
    fake = _FakeGitHub()
    await _app(pem, fake).installation_token(
        INSTALLATION_ID, permissions=READ_ONLY_PERMISSIONS, repositories=["repo"]
    )
    body = json.loads(fake.requests[0].content)
    assert body == {"permissions": READ_ONLY_PERMISSIONS, "repositories": ["repo"]}


@pytest.mark.parametrize(
    ("permissions", "error"),
    [({}, "at least one"), ({"pull_request": "read"}, "pull_request")],
    ids=["empty", "typo"],
)
async def test_permissions_that_would_widen_the_token_are_refused(
    pem, permissions, error
):
    fake = _FakeGitHub()
    app = _app(pem, fake)
    with pytest.raises(ValueError, match=error):
        await app.installation_token(INSTALLATION_ID, permissions=permissions)
    with pytest.raises(ValueError, match=error):
        app.as_installation(INSTALLATION_ID, permissions=permissions)
    assert fake.requests == []


async def test_installation_id_defaults_to_the_settings(pem):
    fake = _FakeGitHub()
    await _app(pem, fake, installation_id=789).installation_token(
        permissions=READ_ONLY_PERMISSIONS
    )
    assert fake.requests[0].url.path == "/app/installations/789/access_tokens"


async def test_installation_id_is_required_somewhere(pem):
    with pytest.raises(ValueError, match="GITHUB_INSTALLATION_ID"):
        await _app(pem, _FakeGitHub()).installation_token(
            permissions=READ_ONLY_PERMISSIONS
        )


async def test_fresh_installation_token_is_reused(pem):
    fake = _FakeGitHub(expires_in=timedelta(hours=1))
    app = _app(pem, fake)

    first = await app.installation_token(
        INSTALLATION_ID, permissions=READ_ONLY_PERMISSIONS
    )
    second = await app.installation_token(
        INSTALLATION_ID, permissions=READ_ONLY_PERMISSIONS
    )

    assert first == second
    assert len(fake.mint_requests) == 1


async def test_near_expiry_installation_token_is_refreshed(pem):
    fake = _FakeGitHub(expires_in=timedelta(minutes=2))
    app = _app(pem, fake)

    first = await app.installation_token(
        INSTALLATION_ID, permissions=READ_ONLY_PERMISSIONS
    )
    second = await app.installation_token(
        INSTALLATION_ID, permissions=READ_ONLY_PERMISSIONS
    )

    assert (first.token, second.token) == ("ghs_1", "ghs_2")
    assert len(fake.mint_requests) == 2


async def test_concurrent_callers_share_one_mint(pem):
    fake = _FakeGitHub()
    app = _app(pem, fake)

    tokens = await asyncio.gather(
        *(
            app.installation_token(INSTALLATION_ID, permissions=READ_ONLY_PERMISSIONS)
            for _ in range(5)
        )
    )

    assert len(set(tokens)) == 1
    assert len(fake.mint_requests) == 1


async def test_installation_tokens_are_cached_per_scope(pem):
    fake = _FakeGitHub()
    app = _app(pem, fake)

    read_only = await app.installation_token(
        INSTALLATION_ID, permissions=READ_ONLY_PERMISSIONS
    )
    read_write = await app.installation_token(
        INSTALLATION_ID, permissions=READ_WRITE_PERMISSIONS
    )
    again = await app.installation_token(
        INSTALLATION_ID, permissions=dict(reversed(READ_ONLY_PERMISSIONS.items()))
    )

    assert read_only != read_write
    assert again == read_only
    assert len(fake.mint_requests) == 2


async def test_installation_client_uses_a_downscoped_token(pem):
    fake = _FakeGitHub()
    gh = _app(pem, fake).as_installation(
        INSTALLATION_ID, permissions=READ_ONLY_PERMISSIONS
    )

    await gh.arequest("GET", "/repos/owner/repo/pulls/1")

    mint, call = fake.requests
    assert json.loads(mint.content) == {"permissions": READ_ONLY_PERMISSIONS}
    assert call.url.path == "/repos/owner/repo/pulls/1"
    assert call.headers["Authorization"] == "token ghs_1"


@pytest.mark.parametrize(
    ("expires_in", "mints"),
    [(timedelta(hours=1), 1), (timedelta(seconds=30), 2)],
    ids=["fresh", "near-expiry"],
)
async def test_installation_client_refreshes_its_token(pem, expires_in, mints):
    fake = _FakeGitHub(expires_in=expires_in)
    gh = _app(pem, fake).as_installation(
        INSTALLATION_ID, permissions=READ_ONLY_PERMISSIONS
    )

    await gh.arequest("GET", "/repos/owner/repo/pulls/1")
    await gh.arequest("GET", "/repos/owner/repo/pulls/1")

    assert len(fake.mint_requests) == mints
    assert fake.requests[-1].headers["Authorization"] == f"token ghs_{mints}"
