"""Signed links and write-gating nonces for the public feedback page.

Run ids are already UUIDv4, so the token's signature isn't hiding them — it
lets the endpoint reject junk before touching the database.

The nonce is separate: minted when the page renders, required on the write.
Bugmail reaches every CC'd account and corporate mail scanners pre-fetch every
link they see, so a GET must never record a vote, and a client that never
rendered the page has no nonce to submit. It does not stop a determined
scripted attacker; the per-run cap and the partial unique indexes in
``database/models.py`` bound that.

HMAC payloads are domain-prefixed so a signature minted for one purpose can't
be replayed as another.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from uuid import UUID

from app.config import settings

log = logging.getLogger(__name__)

_SIG_CHARS = 32


def is_enabled() -> bool:
    """Whether a link can be minted: unset config means no footer at all."""
    return bool(settings.feedback_token_secret and settings.feedback_public_base_url)


def _sign(domain: str, payload: str) -> str:
    return hmac.new(
        settings.feedback_token_secret.encode(),
        f"{domain}:{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()[:_SIG_CHARS]


def mint_token(run_id: UUID) -> str:
    return f"{run_id.hex}.{_sign('token', run_id.hex)}"


def verify_token(token: str) -> UUID | None:
    """Return the run id a token attests to, or None if it doesn't verify."""
    if not settings.feedback_token_secret:
        return None
    raw, _, signature = token.partition(".")
    if not signature or not hmac.compare_digest(signature, _sign("token", raw)):
        return None
    try:
        return UUID(hex=raw)
    except ValueError:
        return None


def feedback_url(run_id: UUID) -> str:
    """Public rating URL for a run.

    ``/rate`` is the only prefix hackbot-ui exempts from SSO, so the path has to
    stay in that namespace — see its middleware.ts.
    """
    base = settings.feedback_public_base_url.rstrip("/")
    return f"{base}/rate/{mint_token(run_id)}"


def mint_nonce(run_id: UUID) -> str:
    issued_at = int(time.time())
    return f"{issued_at}.{_sign('nonce', f'{run_id.hex}:{issued_at}')}"


def verify_nonce(run_id: UUID, nonce: str) -> bool:
    if not settings.feedback_token_secret:
        return False
    issued_raw, _, signature = nonce.partition(".")
    if not signature:
        return False
    try:
        issued_at = int(issued_raw)
    except ValueError:
        return False
    if time.time() - issued_at > settings.feedback_nonce_ttl_seconds:
        return False
    expected = _sign("nonce", f"{run_id.hex}:{issued_at}")
    return hmac.compare_digest(signature, expected)


def anon_id(
    rater_key: str | None, client_ip: str | None, user_agent: str | None
) -> str | None:
    """Stable pseudonymous key for deduping anonymous raters.

    Prefers ``rater_key``, a per-browser cookie id. IP + user agent is only a
    fallback because alone it is unsafe: colleagues behind one office or VPN
    egress IP on the same Firefox build hash identically, and since the write
    is an upsert the second would silently overwrite the first.
    """
    if rater_key:
        return _sign("anon", f"key:{rater_key}")
    if not client_ip and not user_agent:
        return None
    return _sign("anon", f"{client_ip or ''}|{user_agent or ''}")


def client_ip_from(forwarded_for: str | None) -> str | None:
    """First hop in an X-Forwarded-For chain: the original client.

    Spoofable, which is acceptable only because this feeds soft dedupe rather
    than any authorization decision.
    """
    if not forwarded_for:
        return None
    return forwarded_for.split(",")[0].strip() or None
