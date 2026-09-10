import datetime

import jwt
import pytest
from bugzilla_proxy.config import Settings
from bugzilla_proxy.tokens import TOKEN_AUDIENCE
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

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


def _self_signed_cert(key, subject: str) -> str:
    """An x509 cert wrapping ``key``, in the shape Google publishes."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    now = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture(scope="session")
def keypair() -> tuple[str, str]:
    """A throwaway RSA keypair standing in for the service account's."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return _private_pem(key), _public_pem(key)


@pytest.fixture(scope="session")
def signing_cert(keypair) -> str:
    """The public half as an x509 cert, which is how Google serves it."""
    private_pem, _public = keypair
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return _self_signed_cert(key, ISSUER)


@pytest.fixture
def settings(keypair) -> Settings:
    """Local mode: a static PEM, no service account to verify against."""
    _private, public_pem = keypair
    return Settings(
        upstream_url="https://bugzilla.example.com/rest",
        upstream_api_key="upstream-key",
        jwt_public_key=public_pem,
        token_issuer="",
    )


@pytest.fixture
def sa_settings() -> Settings:
    """Deployed mode: verify against the issuer's published certificates."""
    return Settings(
        upstream_url="https://bugzilla.example.com/rest",
        upstream_api_key="upstream-key",
        jwt_public_key="",
        token_issuer=ISSUER,
    )


@pytest.fixture
def served_certs(monkeypatch, signing_cert):
    """Stand in for Google's per-account certificate endpoint.

    Returns a dict the test can mutate (to simulate rotation) plus a call log
    and a way to make the endpoint fail.
    """
    state = {"certs": {KEY_ID: signing_cert}, "calls": [], "fail": False}

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_get(url, timeout=None):
        state["calls"].append(url)
        if state["fail"]:
            import httpx

            raise httpx.ConnectError("boom")
        return FakeResponse(state["certs"])

    import bugzilla_proxy.tokens as tokens_module

    monkeypatch.setattr(tokens_module.httpx, "get", fake_get)
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
