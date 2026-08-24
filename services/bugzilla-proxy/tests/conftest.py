import json

import jwt
import pytest
from bugzilla_proxy.config import Settings
from bugzilla_proxy.tokens import TOKEN_AUDIENCE
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "hackbot-api@example-project.iam.gserviceaccount.com"
# Must match bugzilla_proxy.tokens.TOKEN_AUDIENCE, which is a constant, not
# a setting, so tests mint against the same literal the verifier requires.
AUDIENCE = TOKEN_AUDIENCE
KEY_ID = "abc123"


def _private_pem(key) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _public_pem(key) -> str:
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


def _jwk(public_pem: str, kid: str) -> dict:
    """One JWK for ``public_pem``, in the shape Google's JWKS carries."""
    key = serialization.load_pem_public_key(public_pem.encode())
    entry = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key))
    entry.update({"kid": kid, "alg": "RS256", "use": "sig"})
    return entry


@pytest.fixture(scope="session")
def keypair() -> tuple[str, str]:
    """A throwaway RSA keypair standing in for the service account's."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _private_pem(key), _public_pem(key)


@pytest.fixture(scope="session")
def signing_jwk(keypair) -> dict:
    """The public half as a JWK, which is how Google serves it."""
    _private, public_pem = keypair
    return _jwk(public_pem, KEY_ID)


@pytest.fixture
def settings(keypair) -> Settings:
    """Local mode: a static PEM, no service account to verify against."""
    _private, public_pem = keypair
    return Settings(
        bugzilla_api_url="https://bugzilla.example.com/rest",
        bugzilla_api_key="upstream-key",
        jwt_public_key=public_pem,
        token_issuer="",
    )


@pytest.fixture
def sa_settings() -> Settings:
    """Deployed mode: verify against the issuer's published certificates."""
    return Settings(
        bugzilla_api_url="https://bugzilla.example.com/rest",
        bugzilla_api_key="upstream-key",
        jwt_public_key="",
        token_issuer=ISSUER,
    )


@pytest.fixture
def served_certs(monkeypatch, signing_jwk):
    """Stand in for Google's per-account JWKS endpoint.

    Intercepts `PyJWKClient.fetch_data`, the one place it reaches the network.
    Returns state the test can mutate to simulate rotation or an outage, plus
    the URLs it was asked for.
    """
    state = {"keys": [signing_jwk], "calls": [], "fail": False}

    def fake_fetch(self):
        state["calls"].append(self.uri)
        if state["fail"]:
            raise jwt.PyJWKClientConnectionError("boom")
        data = {"keys": state["keys"]}
        # The real fetch_data populates the cache; without this the client
        # would refetch every time and the caching tests would be vacuous.
        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(data)
        return data

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fake_fetch)
    return state


@pytest.fixture
def mint(keypair):
    """Sign a token the way hackbot-api's local path does (no key id)."""
    private_pem, _public = keypair

    def _mint(bz: dict, *, headers: dict | None = None, **overrides) -> str:
        claims = {
            "iss": ISSUER,
            "aud": AUDIENCE,
            "sub": "run:11111111-2222-3333-4444-555555555555",
            "jti": "token-1",
            "iat": 1_700_000_000,
            "exp": 4_000_000_000,
            "agent": "frontend-triage",
            "requested_by": "someone@mozilla.com",
            "bz": bz,
        }
        claims.update(overrides)
        return jwt.encode(claims, private_pem, algorithm="RS256", headers=headers)

    return _mint


@pytest.fixture
def mint_sa(mint):
    """Sign the way IAM does: same key, carrying a key id in the header."""

    def _mint(bz: dict, *, kid: str = KEY_ID, **overrides) -> str:
        return mint(bz, headers={"kid": kid}, **overrides)

    return _mint


@pytest.fixture
def public_scope() -> dict:
    """The shape phase 0 issues: public bugs, whole-bug reads, no attachments."""
    return {
        "read_only": True,
        "confidential": False,
        "attachments": False,
        "filter_content": "off",
        "grants": [
            {
                "tier": "full",
                "anchor": {},
                "endpoints": ["bug", "bug/*/comment"],
            }
        ],
    }
