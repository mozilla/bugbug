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


class TokenError(Exception):
    """A token that cannot be trusted, or asks for something unsupported."""


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
        self._jwks: jwt.PyJWKClient | None = None
        if settings.token_issuer:
            self._jwks = jwt.PyJWKClient(
                CERTS_URL_TEMPLATE.format(service_account=settings.token_issuer),
                lifespan=settings.jwt_public_key_ttl_seconds,
            )

    def _key_for(self, token: str) -> Any:
        if self._jwks is None:
            return self._settings.jwt_public_key
        try:
            return self._jwks.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientError as exc:
            log.warning("No usable signing key for token: %s", exc)
            raise TokenError("token is not signed by a key we trust") from exc

    def verify(self, token: str) -> Scope:
        if not token:
            raise TokenError("no token presented")

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
            # Terse to the caller; detail goes to the log.
            log.warning("Rejected token: %s", exc)
            raise TokenError("token is not valid") from exc
        return parse_scope(claims)
