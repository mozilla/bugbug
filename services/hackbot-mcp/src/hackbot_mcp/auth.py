"""Google sign-in, restricted to verified @mozilla.com accounts."""

import logging
from typing import Any

import httpx
from fastmcp.server.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider, GoogleTokenVerifier
from fastmcp.server.dependencies import get_access_token
from key_value.aio.protocols import AsyncKeyValue

log = logging.getLogger(__name__)

ALLOWED_DOMAIN = "mozilla.com"

SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]


class UnauthorizedError(Exception):
    """The request carries no identity this server accepts."""


def mozilla_email(claims: dict[str, Any]) -> str | None:
    """The caller's email if the claims are a verified Mozilla account, else None.

    Three checks, each of which alone is not enough:
    - `hd` is the Workspace domain of the account. Only Google's userinfo
      endpoint returns it (tokeninfo does not), and FastMCP ignores a failed
      userinfo call, so a missing `hd` must fail closed.
    - `email_verified`, which tokeninfo returns as the string "true".
    - the email's own domain, so an account of the domain can't be signed in
      under an alias of another one.
    """
    user_data = claims.get("google_user_data") or {}
    if user_data.get("hd") != ALLOWED_DOMAIN:
        return None
    if claims.get("email_verified") not in (True, "true"):
        return None
    email = claims.get("email")
    if not isinstance(email, str):
        return None
    email = email.strip().lower()
    local, at, domain = email.rpartition("@")
    if not local or not at or domain != ALLOWED_DOMAIN:
        return None
    return email


class MozillaTokenVerifier(GoogleTokenVerifier):
    """Google's verifier, accepting only verified accounts of `ALLOWED_DOMAIN`."""

    def __init__(self, *, client_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client_id = client_id

    async def verify_token(self, token: str) -> AccessToken | None:
        access_token = await super().verify_token(token)
        if access_token is None:
            return None
        claims = access_token.claims
        # The token must have been issued to this server's own OAuth client.
        if claims.get("aud") != self._client_id:
            log.warning("Rejected a Google token issued to another client")
            return None
        if mozilla_email(claims) is None:
            log.info("Rejected a Google account outside %s", ALLOWED_DOMAIN)
            return None
        return access_token


class MozillaGoogleProvider(GoogleProvider):
    """FastMCP's Google OAuth proxy, with `MozillaTokenVerifier` checking tokens.

    The proxy validates the upstream Google token on every request, through
    the verifier, so swapping the verifier is what restricts every request.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: str,
        jwt_signing_key: str,
        client_storage: AsyncKeyValue,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            required_scopes=SCOPES,
            jwt_signing_key=jwt_signing_key,
            client_storage=client_storage,
            # Only a hint for Google's account picker; the verifier decides.
            extra_authorize_params={"hd": ALLOWED_DOMAIN},
            http_client=http_client,
        )
        verifier = MozillaTokenVerifier(
            client_id=client_id,
            required_scopes=SCOPES,
            http_client=http_client,
        )
        # GoogleProvider builds its verifier itself. tests/test_auth.py pins
        # that this attribute is the one the proxy checks tokens with, and
        # every tool re-checks the claims (`current_user_email`) regardless.
        if not isinstance(getattr(self, "_token_validator", None), GoogleTokenVerifier):
            raise RuntimeError("FastMCP's OAuthProxy no longer has _token_validator")
        self._token_validator = verifier


def current_user_email() -> str:
    """The authenticated caller's email, re-checked against the domain rules."""
    token = get_access_token()
    email = mozilla_email(token.claims) if token is not None else None
    if email is None:
        raise UnauthorizedError(
            f"Sign in with a verified @{ALLOWED_DOMAIN} Google account."
        )
    return email
