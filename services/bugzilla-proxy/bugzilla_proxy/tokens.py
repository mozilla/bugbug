"""Verifying the per-run capability token and turning it into a Scope.

hackbot-api signs each token with its own service account's Google-managed key.
We verify against the certificates Google publishes for that account, so no key
material is generated, exchanged or rotated on either side. Locally a PEM stands
in, since there is no service account.

Everything a request may do comes from the token, so this module fails closed:
an unknown endpoint pattern, an unimplemented content filter or a private grant
without a structural anchor is an error, not a warning.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives import serialization as _serialization
from cryptography.x509 import load_pem_x509_certificate

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

# The root of trust for every token, and a constant for a sharper reason than
# the audience: point it elsewhere and whoever answers decides which signatures
# we accept, which is an authentication bypass rather than a degradation.
CERTS_URL_TEMPLATE = (
    "https://www.googleapis.com/robot/v1/metadata/x509/{service_account}"
)


class TokenError(Exception):
    """A token that cannot be trusted, or asks for something unsupported."""


class PublicKeySource:
    """The issuer's public keys by key id, TTL-cached.

    A fetch rather than configuration because Google rotates these on its own
    schedule. The URL is issuer-specific, so fetching from it *is* the issuer
    binding: another account's token has no key here to verify against.
    """

    def __init__(self, settings: Settings) -> None:
        if bool(settings.jwt_public_key) == bool(settings.token_issuer):
            raise ValueError(
                "configure exactly one of token_issuer (the signing service "
                "account's email, for a deployment) or jwt_public_key (a PEM, "
                "for local runs)"
            )
        self._settings = settings
        self._cached: dict[str, str] | None = None
        self._expires_at = 0.0

    @property
    def is_local(self) -> bool:
        return bool(self._settings.jwt_public_key)

    def get(self, kid: str | None) -> str:
        """The PEM for ``kid``, refetching once if unknown.

        An unknown id usually means Google rotated since our last fetch, so one
        immediate refetch beats failing every request for the rest of the TTL.
        """
        if self.is_local:
            return self._settings.jwt_public_key

        keys = self._current()
        if kid is None:
            raise TokenError("token carries no key id")
        if kid not in keys:
            keys = self._refresh()
        if kid not in keys:
            raise TokenError(f"unknown signing key {kid!r}")
        return keys[kid]

    def _current(self) -> dict[str, str]:
        if self._cached is not None and time.monotonic() < self._expires_at:
            return self._cached
        return self._refresh()

    def _refresh(self) -> dict[str, str]:
        url = CERTS_URL_TEMPLATE.format(service_account=self._settings.token_issuer)
        try:
            response = httpx.get(url, timeout=10.0)
            response.raise_for_status()
            certs = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # A stale cache beats failing every run's reads over a blip.
            if self._cached is not None:
                log.warning("Could not refresh signing certs, using cached: %s", exc)
                return self._cached
            raise TokenError("cannot fetch the issuer's signing certificates") from exc

        # Google serves `{key_id: x509 PEM}`. Convert once so the hot path is
        # a dict lookup.
        self._cached = {
            kid: load_pem_x509_certificate(pem.encode())
            .public_key()
            .public_bytes(
                encoding=_serialization.Encoding.PEM,
                format=_serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
            for kid, pem in certs.items()
        }
        self._expires_at = time.monotonic() + self._settings.jwt_public_key_ttl_seconds
        return self._cached


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
    """Verifies a token's signature and claims, then parses it into a Scope."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._keys = PublicKeySource(settings)

    def verify(self, token: str) -> Scope:
        if not token:
            raise TokenError("no token presented")

        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError as exc:
            log.warning("Rejected token with an unreadable header: %s", exc)
            raise TokenError("token is not valid") from exc

        key = self._keys.get(kid)

        # Locally the single configured key is the whole trust anchor, so
        # there is nothing sensible to check `iss` against.
        expected_issuer = None if self._keys.is_local else self._settings.token_issuer

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
