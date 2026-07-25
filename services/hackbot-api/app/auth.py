import hashlib
import hmac
import logging

from fastapi import Header, HTTPException, Request, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import settings

log = logging.getLogger(__name__)


def verify_phabricator_signature(raw_body: bytes, signature: str | None) -> bool:
    """Constant-time-check Phabricator's `X-Phabricator-Webhook-Signature`.

    Phabricator signs each delivery with HMAC-SHA256 over the raw request body,
    keyed by the webhook's HMAC key, and sends the hex digest in the header.
    Returns False if the secret is unconfigured or the header is missing/wrong.
    """
    if not settings.webhook.secret or not signature:
        return False
    expected = hmac.new(
        settings.webhook.secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def require_phabricator_signature(
    request: Request,
    x_phabricator_webhook_signature: str | None = Header(default=None),
) -> None:
    """Reject the request unless Phabricator's webhook signature is valid.

    A dependency mirroring `require_api_key`, but it authenticates via an HMAC
    over the raw body rather than a header token. Reading the body here is safe:
    Starlette caches it, so the route can still call `request.json()`.
    """
    raw = await request.body()
    if not verify_phabricator_signature(raw, x_phabricator_webhook_signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook signature",
        )


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.external_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key not configured",
        )
    if x_api_key is None or not hmac.compare_digest(
        x_api_key, settings.external_api_key
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key",
        )


async def require_push_auth(authorization: str | None = Header(default=None)) -> None:
    """Verify a Google-signed OIDC token from an Eventarc/Pub/Sub push request.

    Cloud Run allows unauthenticated invocations for this service (that's how
    `require_api_key` callers reach it at all), so platform-level IAM checks on
    the push subscription/Eventarc trigger don't protect these routes on their
    own — the token still needs verifying here, same as GCP's own docs recommend
    for push endpoints on a service that isn't otherwise locked down.
    """
    if not settings.push_auth_audience or not settings.push_auth_service_account:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Push auth not configured",
        )
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    token = authorization.removeprefix("Bearer ")
    try:
        claims = id_token.verify_oauth2_token(
            token, google_requests.Request(), audience=settings.push_auth_audience
        )
    except ValueError:
        log.warning("Rejected push request with invalid OIDC token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from None
    if claims.get("email") != settings.push_auth_service_account:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token not from the expected service account",
        )
