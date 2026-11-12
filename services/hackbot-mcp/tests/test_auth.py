"""Sign-in: only verified @mozilla.com Google accounts get through."""

import httpx
import pytest
from conftest import USER, claims, settings
from fastmcp.server.auth.providers.google import GoogleTokenVerifier
from hackbot_mcp import auth
from hackbot_mcp.auth import MozillaGoogleProvider, MozillaTokenVerifier, mozilla_email
from hackbot_mcp.server import create_server
from hackbot_mcp.storage import build_storage

CLIENT_ID = "client-id.apps.googleusercontent.com"


def google(tokeninfo: dict | None, userinfo: dict | None) -> httpx.AsyncClient:
    """Google's tokeninfo and userinfo endpoints, answering with these bodies."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = tokeninfo if request.url.path == "/tokeninfo" else userinfo
        if body is None:
            return httpx.Response(400, json={"error": "invalid_token"})
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def tokeninfo(**overrides) -> dict:
    values = {
        "aud": CLIENT_ID,
        "sub": "1234",
        "scope": "openid https://www.googleapis.com/auth/userinfo.email",
        "email": USER,
        "email_verified": "true",
        "expires_in": "3599",
    }
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


def userinfo(**overrides) -> dict:
    values = {"id": "1234", "email": USER, "verified_email": True, "hd": "mozilla.com"}
    values.update(overrides)
    return {k: v for k, v in values.items() if v is not None}


def verifier(http_client: httpx.AsyncClient) -> MozillaTokenVerifier:
    return MozillaTokenVerifier(
        client_id=CLIENT_ID, required_scopes=auth.SCOPES, http_client=http_client
    )


async def test_a_verified_mozilla_account_is_accepted():
    token = await verifier(google(tokeninfo(), userinfo())).verify_token("t")
    assert token is not None
    assert mozilla_email(token.claims) == USER


@pytest.mark.parametrize(
    ("info", "user"),
    [
        pytest.param(tokeninfo(), userinfo(hd="example.com"), id="other-hd"),
        pytest.param(tokeninfo(), userinfo(hd=None), id="no-hd"),
        pytest.param(tokeninfo(), None, id="userinfo-failed"),
        pytest.param(
            tokeninfo(email="dev@gmail.com"),
            userinfo(email="dev@gmail.com", hd=None),
            id="gmail",
        ),
        pytest.param(
            tokeninfo(email="dev@mozilla.com.evil.example"),
            userinfo(email="dev@mozilla.com.evil.example"),
            id="suffix-lookalike",
        ),
        pytest.param(
            tokeninfo(email_verified="false"),
            userinfo(verified_email=False),
            id="unverified",
        ),
        pytest.param(
            tokeninfo(email_verified=None),
            userinfo(verified_email=None),
            id="verification-unknown",
        ),
        pytest.param(tokeninfo(aud="another-client"), userinfo(), id="other-client"),
        pytest.param(None, userinfo(), id="invalid-token"),
    ],
)
async def test_other_accounts_are_rejected(info, user):
    assert await verifier(google(info, user)).verify_token("t") is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"google_user_data": None},
        {"google_user_data": {"hd": "example.com"}},
        {"email_verified": False},
        {"email_verified": "false"},
        {"email": "dev@example.com"},
        {"email": None},
        {"email": "@mozilla.com"},
    ],
)
def test_mozilla_email_fails_closed(overrides):
    assert mozilla_email(claims(**overrides)) is None


def test_mozilla_email_normalizes_case():
    assert mozilla_email(claims(email=" Dev@Mozilla.COM ")) == USER


def test_the_proxy_checks_tokens_with_the_mozilla_verifier():
    # Pins the FastMCP internal the provider swaps: if a FastMCP upgrade
    # renames it, this fails instead of every Google account being accepted
    # (the tools would still refuse them, but the proxy would not).
    storage, _ = build_storage(settings())
    provider = MozillaGoogleProvider(
        client_id=CLIENT_ID,
        client_secret="secret",
        base_url="https://hackbot-mcp.example",
        jwt_signing_key="j" * 32,
        client_storage=storage,
    )
    assert isinstance(provider._token_validator, MozillaTokenVerifier)
    assert isinstance(provider._token_validator, GoogleTokenVerifier)


async def test_mcp_endpoint_requires_a_token(hackbot):
    app = create_server(settings(), hackbot=hackbot).http_app(stateless_http=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://hackbot-mcp.example"
    ) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        bad = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer not-a-token",
            },
        )
        metadata = await client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 401
    assert "resource_metadata" in response.headers["www-authenticate"]
    assert bad.status_code == 401
    assert metadata.status_code == 200


async def test_tools_refuse_without_a_token(mcp_client):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="mozilla.com"):
        await mcp_client.call_tool("list_agents", {})


def test_storage_is_required_outside_development():
    with pytest.raises(ValueError, match="CLOUD_SQL_INSTANCE or DATABASE_URL"):
        settings(environment="production")
    assert settings(environment="production", cloud_sql_instance="p:r:i")


def test_short_keys_are_refused():
    with pytest.raises(ValueError, match="jwt_signing_key"):
        settings(jwt_signing_key="short")
