import inspect

import pytest
from app import auth
from fastapi import HTTPException
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
    monkeypatch.setattr(auth.settings, "api_audience", "https://hackbot.example")
    monkeypatch.setattr(
        auth.settings,
        "allowed_service_accounts",
        ["listener@example.iam.gserviceaccount.com"],
    )

    with pytest.raises(HTTPException) as exc:
        auth.require_api_key(x_api_key=None, authorization="Bearer bad-token")

    assert exc.value.status_code == 401


def test_google_auth_dependencies_are_synchronous():
    assert not inspect.iscoroutinefunction(auth.require_api_key)
