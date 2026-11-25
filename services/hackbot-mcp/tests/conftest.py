from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.server.auth import AccessToken
from hackbot_client import HackbotClient
from hackbot_mcp import auth
from hackbot_mcp.config import Settings
from hackbot_mcp.server import create_server

API = "https://hackbot-api.example"
UI = "https://hackbot.example"
USER = "dev@mozilla.com"
RUN_ID = "d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6"

BUG_FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "require_review": {"type": "boolean", "default": False},
        "bug_id": {"type": "integer"},
        "revision_id": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "comment": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "bugzilla_needinfo_flag_id": {
            "anyOf": [{"type": "integer", "exclusiveMinimum": 0}, {"type": "null"}]
        },
    },
    "required": ["bug_id"],
}

AGENTS = [
    {
        "name": "bug-fix",
        "description": "Fix a bug.",
        "input_schema": BUG_FIX_SCHEMA,
    }
]


def settings(**overrides: Any) -> Settings:
    values = {
        "base_url": "https://hackbot-mcp.example",
        "google_client_id": "client-id.apps.googleusercontent.com",
        "google_client_secret": "client-secret",
        "jwt_signing_key": "j" * 32,
        "storage_encryption_key": "s" * 32,
        "hackbot_api_url": API,
        "hackbot_api_key": "test-key",
        "hackbot_ui_url": UI,
        "environment": "development",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def run_doc(**overrides: Any) -> dict[str, Any]:
    now = datetime(2026, 11, 12, tzinfo=timezone.utc).isoformat()
    doc = {
        "run_id": RUN_ID,
        "agent": "bug-fix",
        "status": "succeeded",
        "inputs": {"bug_id": 1, "require_review": True},
        "requested_by": USER,
        "requested_via": "hackbot-mcp@example.iam.gserviceaccount.com",
        "dedupe_key": None,
        "created_at": now,
        "updated_at": now,
        "execution_name": "projects/p/executions/e",
        "results_prefix": f"runs/{RUN_ID}",
        "summary": {"status": "ok", "findings": {}, "actions": []},
        "artifacts": [],
        "error": None,
    }
    doc.update(overrides)
    return doc


def claims(**overrides: Any) -> dict[str, Any]:
    values = {
        "sub": "1234",
        "aud": "client-id.apps.googleusercontent.com",
        "email": USER,
        "email_verified": "true",
        "google_user_data": {
            "email": USER,
            "verified_email": True,
            "hd": "mozilla.com",
        },
    }
    values.update(overrides)
    return values


class FakeAPI:
    """hackbot-api behind an httpx MockTransport: routes and recorded requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[
            tuple[str, str], Callable[[httpx.Request], httpx.Response]
        ] = {
            ("GET", "/agents"): lambda r: httpx.Response(200, json=AGENTS),
            ("GET", f"/runs/{RUN_ID}"): lambda r: httpx.Response(200, json=run_doc()),
            ("GET", f"/runs/{RUN_ID}/actions"): lambda r: httpx.Response(200, json=[]),
            ("GET", "/runs"): lambda r: httpx.Response(200, json=[run_doc()]),
        }

    def route(self, method: str, path: str, response: Any) -> None:
        self.routes[(method, path)] = (
            response if callable(response) else (lambda r: response)
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        respond = self.routes.get((request.method, request.url.path))
        if respond is None:
            return httpx.Response(404, json={"detail": "Not found"})
        return respond(request)

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path == path]


@pytest.fixture
def api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture
def hackbot(api: FakeAPI) -> HackbotClient:
    client = HackbotClient(base_url=API, api_key="test-key")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
    return client


@pytest.fixture
def signed_in(monkeypatch) -> Callable[..., None]:
    """Make tools see an access token with the given claims (default: USER)."""

    def sign_in(**overrides: Any) -> None:
        token = AccessToken(
            token="t", client_id="1234", scopes=[], claims=claims(**overrides)
        )
        monkeypatch.setattr(auth, "get_access_token", lambda: token)

    sign_in()
    return sign_in


@pytest.fixture
def server(hackbot: HackbotClient):
    return create_server(settings(), hackbot=hackbot)


@pytest.fixture
async def mcp_client(server):
    async with Client(server) as client:
        yield client
