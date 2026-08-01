"""Signed links and write-gating nonces for the public feedback page.

The feedback URL is published into a Bugzilla comment that anyone on the
internet can read, so the token in it must be unguessable and tamper-evident
while carrying no secret of its own. Run ids are already UUIDv4, so the
signature isn't there to hide them — it's so the endpoint can reject junk
before touching the database.

The nonce is a separate, short-lived value minted when the page renders and
required on the write. Bugmail reaches every CC'd account and corporate mail
scanners pre-fetch every link they see, so a GET must never record a vote; the
nonce makes that structural rather than a convention, since a client that never
rendered the page has nothing to submit. It does not stop a determined scripted
attacker — the per-run cap and the partial unique indexes in
``database/models.py`` are what bound that.

Every HMAC payload is domain-prefixed so a signature minted for one purpose can
never be replayed as another.
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
    """Whether feedback links can be minted at all.

    Both the signing secret and the public base URL are required: a link
    without either would be unverifiable or unreachable, so the applier skips
    the footer entirely rather than posting a broken URL to Bugzilla.
    """
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
    """Return the run id a token attests to, or None if it doesn't verify.

    Callers must treat None as "no such link" rather than distinguishing a
    malformed token from a well-formed but unsigned one, so probing can't learn
    whether a token shape is right.
    """
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

    Lives under ``/rate`` rather than ``/feedback`` because that prefix is the
    one exempted from the UI's SSO middleware: keeping the public surface in its
    own namespace means the internal ratings pages under ``/feedback`` stay
    guarded by default instead of relying on a narrower pattern.
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

    Prefers ``rater_key``, a per-browser id the UI keeps in a first-party
    cookie. IP + user agent is only a fallback, because on its own it is
    actively unsafe here: two people behind one office or VPN egress IP running
    the same Firefox build hash identically, and since the write is an upsert
    the second rater would silently overwrite the first. A cookie distinguishes
    them while still letting one person change their own mind in place.

    Salted with the signing secret and truncated, so neither the raw IP nor the
    cookie value is recoverable from what's stored. Returns None when the
    request carries no signal at all, leaving the dedupe column null — the
    partial unique index then ignores the row rather than collapsing every such
    rater into one bucket.
    """
    if rater_key:
        return _sign("anon", f"key:{rater_key}")
    if not client_ip and not user_agent:
        return None
    return _sign("anon", f"{client_ip or ''}|{user_agent or ''}")


def client_ip_from(forwarded_for: str | None) -> str | None:
    """First hop in an X-Forwarded-For chain, which is the original client.

    Later entries are proxies we added, and the header is attacker-controllable
    in general — acceptable here because this only feeds soft dedupe, never
    authorization.
    """
    if not forwarded_for:
        return None
    return forwarded_for.split(",")[0].strip() or None
