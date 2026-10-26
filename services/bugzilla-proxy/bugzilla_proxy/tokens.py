"""Verifying the per-run capability token and turning it into a Scope.

hackbot-api signs each token with its own service account's Google-managed key.
We verify against the JWKS Google publishes for that account, so no key
material is generated, exchanged or rotated on either side. Locally a PEM stands
in, since there is no service account.

Everything a request may do comes from the token, so this module fails closed:
an unknown endpoint pattern, an unimplemented content filter or a private grant
without a structural anchor is an error, not a warning.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from typing import Any

import jwt

from bugzilla_proxy.config import Settings
from bugzilla_proxy.scope import TIERS, Grant, Scope

log = logging.getLogger(__name__)

# A grant may name a subset of these but may not invent a pattern, so a typo
# cannot quietly widen access and a bare wildcard cannot be smuggled in.
KNOWN_ENDPOINTS = frozenset(
    {
        "bug",
        "bug/*/comment",
        "bug/*/attachment",
        "bug/attachment/*",
    }
)

# `remove` and `llm` land with private access. Until then a token asking for
# one is rejected rather than served unfiltered.
IMPLEMENTED_FILTER_MODES = frozenset({"off"})

# Must match `bz_token.TOKEN_AUDIENCE` in hackbot-api, which mints against the
# same literal. A constant rather than a setting on both sides: see
# docs/hackbot/bugzilla-proxy.md, "Why this is not a Google credential".
TOKEN_AUDIENCE = "hackbot-bugzilla-proxy"

# Google's JWKS for one service account, and the root of trust for every token.
# A constant for a sharper reason than the audience: point it elsewhere and
# whoever answers decides which signatures we accept, which is an
# authentication bypass rather than a degradation.
CERTS_URL_TEMPLATE = (
    "https://www.googleapis.com/service_accounts/v1/jwk/{service_account}"
)


# The least time between refetches forced by an unknown key id. Without it,
# every token carrying a made-up `kid` costs a request to Google. Google
# publishes a key before signing with it, so the refetch an unknown id does
# trigger also picks up any genuinely new key.
MIN_FORCED_REFETCH_SECONDS = 30.0


class TokenError(Exception):
    """A token that cannot be trusted, or asks for something unsupported.

    The message is returned to the caller, so it may describe the token but
    never anything learned from Bugzilla.
    """


class KeySourceUnavailable(TokenError):
    """The issuer's signing keys could not be fetched.

    Still a refusal, since nothing can be trusted without them, but an outage
    rather than a bad token, so the caller is told to retry.
    """


def _require_mapping(value: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TokenError(f"{what} must be an object")
    return value


def _validate_grant(grant: Grant, *, confidential: bool) -> None:
    if grant.tier not in TIERS:
        raise TokenError(f"unknown tier {grant.tier!r}")
    if not grant.endpoints:
        raise TokenError("a grant must name at least one endpoint")
    unknown = set(grant.endpoints) - KNOWN_ENDPOINTS
    if unknown:
        raise TokenError(f"unknown endpoint pattern(s): {sorted(unknown)}")
    if not grant.is_private:
        return
    if not grant.anchor.has_structural_rule():
        raise TokenError(
            "a grant with security groups needs at least one structural rule "
            "(static_bugs, product, component, status, resolution, "
            "created_after); keyword, whiteboard and blocks rules may only "
            "narrow one, since anyone with editbugs can change them"
        )
    if not confidential:
        raise TokenError(
            "a grant with security groups requires the run to be marked "
            "confidential, so the containment rules apply downstream"
        )


def parse_scope(claims: Mapping[str, Any]) -> Scope:
    """Turn verified JWT claims into a :class:`Scope`, or reject them."""
    bz = _require_mapping(claims.get("bz"), "the 'bz' claim")

    subject = str(claims.get("sub") or "")
    if not subject.startswith("run:"):
        raise TokenError("'sub' must be of the form 'run:<run_id>'")
    run_id = subject.removeprefix("run:")
    if not run_id:
        raise TokenError("'sub' carries no run id")

    jti = str(claims.get("jti") or "")
    if not jti:
        raise TokenError("'jti' is required, it keys the decision cache")

    if not bool(bz.get("read_only", True)):
        raise TokenError("this proxy serves reads only; 'read_only' must be true")

    filter_content = str(bz.get("filter_content", "off"))
    if filter_content not in IMPLEMENTED_FILTER_MODES:
        raise TokenError(
            f"content filtering mode {filter_content!r} is not implemented yet"
        )

    confidential = bool(bz.get("confidential", False))

    raw_grants = bz.get("grants") or []
    if not isinstance(raw_grants, list) or not raw_grants:
        raise TokenError("'grants' must be a non-empty list")

    grants = []
    for raw in raw_grants:
        grant = Grant.from_claim(_require_mapping(raw, "each grant"))
        _validate_grant(grant, confidential=confidential)
        grants.append(grant)

    scope = Scope(
        run_id=run_id,
        agent=str(claims.get("agent") or ""),
        jti=jti,
        requested_by=claims.get("requested_by") or None,
        read_only=True,
        confidential=confidential,
        attachments=bool(bz.get("attachments", False)),
        filter_content=filter_content,
        promotions_max=int(bz.get("promotions_max", 0) or 0),
        grants=tuple(grants),
    )

    # Already covered per-grant above; restated so the invariant is findable.
    if scope.is_private and not scope.confidential:
        raise TokenError("a private scope requires a confidential run")

    return scope


class TokenVerifier:
    """Verifies a token's signature and claims, then parses it into a Scope.

    Deployed, keys come from :class:`jwt.PyJWKClient` pointed at the issuer's
    JWKS, which handles the fetch, the TTL cache, and refetching once when a
    token arrives with a key id we have not seen (Google rotates on its own
    schedule). Because the URL is issuer-specific, using it *is* the issuer
    binding: another account's token has no candidate key at all.
    """

    def __init__(self, settings: Settings) -> None:
        if bool(settings.jwt_public_key) == bool(settings.token_issuer):
            raise ValueError(
                "configure exactly one of token_issuer (the signing service "
                "account's email, for a deployment) or jwt_public_key (a PEM, "
                "for local runs)"
            )
        self._settings = settings
        self._refetch_lock = threading.Lock()
        self._last_forced_refetch = float("-inf")
        self._jwks: jwt.PyJWKClient | None = None
        if settings.token_issuer:
            self._jwks = jwt.PyJWKClient(
                CERTS_URL_TEMPLATE.format(service_account=settings.token_issuer),
                lifespan=settings.jwt_public_key_ttl_seconds,
            )

    def _cached_key(self, kid: str, *, refresh: bool) -> Any | None:
        assert self._jwks is not None
        for key in self._jwks.get_signing_keys(refresh=refresh):
            if key.key_id == kid:
                return key.key
        return None

    def _may_force_refetch(self) -> bool:
        with self._refetch_lock:
            now = time.monotonic()
            if now - self._last_forced_refetch < MIN_FORCED_REFETCH_SECONDS:
                return False
            self._last_forced_refetch = now
            return True

    def _key_for(self, token: str) -> Any:
        if self._jwks is None:
            return self._settings.jwt_public_key
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            log.warning("Rejected token: %s", exc)
            raise TokenError("token is malformed, not a signed JWT") from exc
        if not kid:
            raise TokenError("token is not signed by a key we trust")
        try:
            key = self._cached_key(kid, refresh=False)
            if key is None and self._may_force_refetch():
                key = self._cached_key(kid, refresh=True)
        except jwt.PyJWKClientConnectionError as exc:
            log.warning("Could not fetch the issuer's signing keys: %s", exc)
            raise KeySourceUnavailable(
                "the issuer's signing keys are unavailable, retry shortly"
            ) from exc
        except jwt.PyJWKClientError as exc:
            log.warning("No usable signing key for token: %s", exc)
            raise TokenError("token is not signed by a key we trust") from exc
        if key is None:
            log.warning("No signing key with id %r", kid)
            raise TokenError("token is not signed by a key we trust")
        return key

    def verify(self, token: str) -> Scope:
        if not token:
            raise TokenError(
                "no token presented; send the run's token in the "
                "X-Bugzilla-API-Key header"
            )

        key = self._key_for(token)

        # Locally the single configured key is the whole trust anchor, so
        # there is nothing sensible to check `iss` against.
        expected_issuer = self._settings.token_issuer or None

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=TOKEN_AUDIENCE,
                issuer=expected_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            log.warning("Rejected token: %s", exc)
            raise TokenError(_reason(exc)) from exc
        return parse_scope(claims)


def _reason(exc: jwt.PyJWTError) -> str:
    """Why a token failed, in terms the caller can act on.

    Each reason is about the token alone. None helps forge one: the signature
    still has to verify against a key only hackbot-api's account holds.
    """
    if isinstance(exc, jwt.ExpiredSignatureError):
        return (
            "token has expired; tokens last for one run, so start a new run "
            "for a fresh one"
        )
    if isinstance(exc, (jwt.ImmatureSignatureError, jwt.InvalidIssuedAtError)):
        return "token is not valid yet; check the issuing host's clock"
    if isinstance(exc, jwt.InvalidAudienceError):
        return "token was minted for a different service"
    if isinstance(exc, jwt.InvalidIssuerError):
        return "token was issued by an account this proxy does not trust"
    if isinstance(exc, jwt.MissingRequiredClaimError):
        return f"token is missing the required '{exc.claim}' claim"
    if isinstance(exc, jwt.InvalidSignatureError):
        return "token signature does not verify"
    if isinstance(exc, jwt.DecodeError):
        return "token is malformed, not a signed JWT"
    return "token is not valid"
