import inspect

import pytest
from app import auth
from fastapi import HTTPException, Request
from google.auth import exceptions as google_auth_exceptions
from google.oauth2 import id_token


@pytest.mark.parametrize(
    "error",
    [
        ValueError(),
        google_auth_exceptions.GoogleAuthError(),
        google_auth_exceptions.TransportError(),
    ],
)
def test_google_token_errors_return_401(monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(id_token, "verify_oauth2_token", fail)
    monkeypatch.setattr(auth.settings, "hackbot_api_url", "https://hackbot.example")
    monkeypatch.setattr(
        auth.settings,
        "service_account_roles",
        {"listener@example.iam.gserviceaccount.com": "write"},
    )

    with pytest.raises(HTTPException) as exc:
        auth.require_api_key(
            Request({"type": "http", "method": "GET"}),
            x_api_key=None,
            authorization="Bearer bad-token",
        )

    assert exc.value.status_code == 401


def test_google_auth_dependencies_are_synchronous():
    assert not inspect.iscoroutinefunction(auth.require_api_key)


def test_read_only_service_account_cannot_write(monkeypatch):
    monkeypatch.setattr(
        id_token,
        "verify_oauth2_token",
        lambda *_args, **_kwargs: {"email": "reader@example.iam.gserviceaccount.com"},
    )
    monkeypatch.setattr(
        auth.settings,
        "service_account_roles",
        {"reader@example.iam.gserviceaccount.com": "read"},
    )

    def call(method):
        auth.require_api_key(
            Request({"type": "http", "method": method}),
            x_api_key=None,
            authorization="Bearer token",
        )

    call("GET")
    with pytest.raises(HTTPException) as exc:
        call("POST")

    assert exc.value.status_code == 403
