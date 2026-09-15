import hashlib
import hmac
import logging

from fastapi import Header, HTTPException, Request, status
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from slack_sdk.signature import SignatureVerifier

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


def require_bugzilla_webhook_secret(
    x_bugzilla_webhook_secret: str = Header(),
) -> None:
    """Reject requests without the dedicated Bugzilla webhook secret."""
    if not hmac.compare_digest(
        x_bugzilla_webhook_secret, settings.bugzilla_webhook.secret
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Bugzilla webhook secret",
        )


async def require_slack_signature(
    request: Request,
    x_slack_request_timestamp: str = Header(),
    x_slack_signature: str = Header(),
) -> None:
    verifier = SignatureVerifier(settings.slack.signing_secret)
    raw = await request.body()
    try:
        valid = verifier.is_valid(raw, x_slack_request_timestamp, x_slack_signature)
    except ValueError:
        valid = False

    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Slack signature",
        )


def require_api_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    """Accept either API key (X-API-Key) or service account token (Bearer)."""
    if x_api_key is not None:
        if settings.external_api_key and hmac.compare_digest(
            x_api_key, settings.external_api_key
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-API-Key",
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing credentials",
        )

    try:
        claims = id_token.verify_oauth2_token(
            authorization.removeprefix("Bearer "),
            google_requests.Request(),
            audience=settings.api_audience,
        )
    except (ValueError, google_auth_exceptions.GoogleAuthError):
        log.warning("Rejected request with invalid OIDC credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        ) from None

    if claims.get("email") not in settings.allowed_service_accounts:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Service account not authorized",
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
