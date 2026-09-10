"""Minting per-run Bugzilla capability tokens.

The token is the whole authorization story for a run's Bugzilla access, so
these tests care most about what must *not* be possible: a caller influencing
the scope, a token outliving its run, or a token reaching a container that
should not have one.
"""

import base64
import json

import pytest
from app import bz_token, jobs
from app.bz_token import PUBLIC_READ_SCOPE, BugzillaScope
from app.config import settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture
def signing_key(monkeypatch) -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    monkeypatch.setattr(settings, "bz_token_private_key", pem, raising=False)
    monkeypatch.setattr(settings, "bz_token_service_account", "", raising=False)
    return pem


class Inputs:
    def __init__(self, bug_id: int | None = 1899123) -> None:
        self.bug_id = bug_id


def decode(token: str) -> dict:
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


class TestMint:
    def test_claims_identify_the_run_and_the_requester(self, signing_key):
        token = bz_token.mint(
            run_id="abc",
            agent="frontend-triage",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by="someone@mozilla.com",
        )
        claims = decode(token)
        assert claims["sub"] == "run:abc"
        assert claims["aud"] == bz_token.TOKEN_AUDIENCE
        assert claims["iss"] == bz_token.LOCAL_ISSUER
        assert claims["agent"] == "frontend-triage"
        assert claims["requested_by"] == "someone@mozilla.com"

    def test_the_token_outlives_the_job(self, signing_key):
        claims = decode(
            bz_token.mint(
                run_id="abc",
                agent="a",
                scope=PUBLIC_READ_SCOPE,
                inputs=Inputs(),
                requested_by=None,
            )
        )
        lifetime = claims["exp"] - claims["iat"]
        assert lifetime == (
            settings.job_execution_timeout_seconds + settings.bz_token_grace_seconds
        )

    def test_every_token_gets_its_own_id(self, signing_key):
        def jti() -> str:
            return decode(
                bz_token.mint(
                    run_id="abc",
                    agent="a",
                    scope=PUBLIC_READ_SCOPE,
                    inputs=Inputs(),
                    requested_by=None,
                )
            )["jti"]

        assert jti() != jti()

    def test_the_default_scope_is_read_only_and_public(self, signing_key):
        bz = decode(
            bz_token.mint(
                run_id="abc",
                agent="a",
                scope=PUBLIC_READ_SCOPE,
                inputs=Inputs(),
                requested_by=None,
            )
        )["bz"]
        assert bz["read_only"] is True
        assert bz["confidential"] is False
        assert bz["attachments"] is False
        assert all(not g["anchor"].get("groups") for g in bz["grants"])

    def test_the_signature_verifies_against_the_public_key(self, signing_key):
        """RS256 over `header.payload`, which is what bugzilla-proxy checks."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        token = bz_token.mint(
            run_id="abc",
            agent="a",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by=None,
        )
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}".encode()
        signature = base64.urlsafe_b64decode(
            signature_b64 + "=" * (-len(signature_b64) % 4)
        )
        public_key = serialization.load_pem_private_key(
            signing_key.encode(), password=None
        ).public_key()

        # Raises InvalidSignature if the token was tampered with.
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())

        header = json.loads(
            base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4))
        )
        assert header == {"alg": "RS256", "typ": "JWT"}

    def test_a_tampered_payload_fails_verification(self, signing_key):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        token = bz_token.mint(
            run_id="abc",
            agent="a",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by=None,
        )
        header_b64, payload_b64, signature_b64 = token.split(".")
        claims = decode(token)
        claims["bz"]["grants"][0]["anchor"]["groups"] = ["core-security"]
        forged_payload = (
            base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        )
        signature = base64.urlsafe_b64decode(
            signature_b64 + "=" * (-len(signature_b64) % 4)
        )
        public_key = serialization.load_pem_private_key(
            signing_key.encode(), password=None
        ).public_key()

        with pytest.raises(InvalidSignature):
            public_key.verify(
                signature,
                f"{header_b64}.{forged_payload}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )

    def test_minting_without_a_key_is_an_error(self, monkeypatch):
        monkeypatch.setattr(settings, "bz_token_private_key", "", raising=False)
        monkeypatch.setattr(settings, "bz_token_service_account", "", raising=False)
        with pytest.raises(RuntimeError, match="no signing key"):
            bz_token.mint(
                run_id="abc",
                agent="a",
                scope=PUBLIC_READ_SCOPE,
                inputs=Inputs(),
                requested_by=None,
            )


class TestScopeTemplates:
    def test_the_run_bug_is_substituted_into_the_anchor(self, signing_key):
        scope = BugzillaScope(
            grants=(
                {
                    "tier": "full",
                    "anchor": {"static_bugs": ["$bug_id"], "groups": ["core-security"]},
                    "endpoints": ["bug"],
                },
            ),
            confidential=True,
        )
        bz = decode(
            bz_token.mint(
                run_id="abc",
                agent="a",
                scope=scope,
                inputs=Inputs(bug_id=1899123),
                requested_by=None,
            )
        )["bz"]
        assert bz["grants"][0]["anchor"]["static_bugs"] == [1899123]

    def test_a_template_needing_a_bug_fails_loudly_without_one(self, signing_key):
        """Silently emptying the list would deny everything; dropping it would allow everything."""
        scope = BugzillaScope(
            grants=(
                {
                    "tier": "full",
                    "anchor": {"static_bugs": ["$bug_id"]},
                    "endpoints": ["bug"],
                },
            )
        )
        with pytest.raises(ValueError, match=r"\$bug_id"):
            bz_token.mint(
                run_id="abc",
                agent="a",
                scope=scope,
                inputs=Inputs(bug_id=None),
                requested_by=None,
            )

    def test_the_template_is_not_mutated_between_runs(self, signing_key):
        scope = BugzillaScope(
            grants=(
                {
                    "tier": "full",
                    "anchor": {"static_bugs": ["$bug_id"]},
                    "endpoints": ["bug"],
                },
            )
        )
        for bug_id in (1, 2):
            bz = decode(
                bz_token.mint(
                    run_id="abc",
                    agent="a",
                    scope=scope,
                    inputs=Inputs(bug_id=bug_id),
                    requested_by=None,
                )
            )["bz"]
            assert bz["grants"][0]["anchor"]["static_bugs"] == [bug_id]
        assert scope.grants[0]["anchor"]["static_bugs"] == ["$bug_id"]


class TestBrokerEnv:
    def test_an_agent_with_no_scope_gets_no_token(self, signing_key):
        assert (
            bz_token.broker_env_for(
                run_id="abc",
                agent="a",
                scope=None,
                inputs=Inputs(),
                requested_by=None,
            )
            == {}
        )

    def test_an_unconfigured_deployment_falls_back_silently(self, monkeypatch):
        monkeypatch.setattr(settings, "bz_token_private_key", "", raising=False)
        monkeypatch.setattr(settings, "bz_token_service_account", "", raising=False)
        assert (
            bz_token.broker_env_for(
                run_id="abc",
                agent="a",
                scope=PUBLIC_READ_SCOPE,
                inputs=Inputs(),
                requested_by=None,
            )
            == {}
        )

    def test_a_configured_deployment_emits_the_token(self, signing_key):
        env = bz_token.broker_env_for(
            run_id="abc",
            agent="a",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by=None,
        )
        assert set(env) == {"BUGZILLA_SCOPE_TOKEN"}


class TestBrokerOverrideAllowlist:
    """The broker container is credentialed, so what reaches it is fenced."""

    def test_the_token_is_allowed(self):
        assert "BUGZILLA_SCOPE_TOKEN" in jobs._BROKER_ENV_ALLOWLIST

    def test_anything_else_is_refused(self, monkeypatch):
        monkeypatch.setattr(
            jobs, "_job_resource_name", lambda name: f"projects/p/jobs/{name}"
        )
        with pytest.raises(ValueError, match="refusing to set"):
            jobs._trigger_sync(
                "hackbot-agent-x",
                {"BUG_ID": "1"},
                {"BUGZILLA_API_KEY": "smuggled"},
            )

    def test_no_broker_override_is_added_when_there_is_no_token(self, monkeypatch):
        captured = {}

        class FakeClient:
            def run_job(self, request):
                captured["request"] = request

                class Operation:
                    metadata = type("M", (), {"name": "executions/1"})()

                return Operation()

        monkeypatch.setattr(jobs, "_jobs_client", lambda: FakeClient())
        monkeypatch.setattr(
            jobs, "_job_resource_name", lambda name: f"projects/p/jobs/{name}"
        )
        jobs._trigger_sync("hackbot-agent-x", {"BUG_ID": "1"}, {})
        names = [c.name for c in captured["request"].overrides.container_overrides]
        assert names == ["agent"]

    def test_the_broker_override_targets_the_broker_container(self, monkeypatch):
        captured = {}

        class FakeClient:
            def run_job(self, request):
                captured["request"] = request

                class Operation:
                    metadata = type("M", (), {"name": "executions/1"})()

                return Operation()

        monkeypatch.setattr(jobs, "_jobs_client", lambda: FakeClient())
        monkeypatch.setattr(
            jobs, "_job_resource_name", lambda name: f"projects/p/jobs/{name}"
        )
        jobs._trigger_sync(
            "hackbot-agent-x", {"BUG_ID": "1"}, {"BUGZILLA_SCOPE_TOKEN": "tok"}
        )
        overrides = {
            c.name: {e.name: e.value for e in c.env}
            for c in captured["request"].overrides.container_overrides
        }
        assert overrides["agent"] == {"BUG_ID": "1"}
        assert overrides["broker"] == {"BUGZILLA_SCOPE_TOKEN": "tok"}


SERVICE_ACCOUNT = "hackbot-api@example-project.iam.gserviceaccount.com"


@pytest.fixture
def signing_account(monkeypatch):
    """Deployed mode: sign as the service's own identity, no key material."""
    monkeypatch.setattr(
        settings, "bz_token_service_account", SERVICE_ACCOUNT, raising=False
    )
    monkeypatch.setattr(settings, "bz_token_private_key", "", raising=False)
    return SERVICE_ACCOUNT


@pytest.fixture
def fake_iam(monkeypatch):
    """Stand in for IAM Credentials, recording what we asked it to sign."""
    captured = {}

    class FakeResponse:
        signed_jwt = "signed.by.iam"

    class FakeClient:
        def sign_jwt(self, request):
            captured["request"] = request
            return FakeResponse()

    monkeypatch.setattr(bz_token, "_iam_client", lambda: FakeClient())
    return captured


class TestServiceAccountSigning:
    """Signing as the service's own identity, so no keys are ever exchanged."""

    def test_signing_is_delegated_to_iam(self, signing_account, fake_iam):
        token = bz_token.mint(
            run_id="abc",
            agent="a",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by=None,
        )
        assert token == "signed.by.iam"
        assert (
            fake_iam["request"]["name"]
            == f"projects/-/serviceAccounts/{SERVICE_ACCOUNT}"
        )

    def test_the_signed_payload_carries_the_scope(self, signing_account, fake_iam):
        bz_token.mint(
            run_id="abc",
            agent="frontend-triage",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by="someone@mozilla.com",
        )
        claims = json.loads(fake_iam["request"]["payload"])
        assert claims["sub"] == "run:abc"
        assert claims["agent"] == "frontend-triage"
        assert claims["bz"]["read_only"] is True

    def test_the_issuer_is_the_signing_account(self, signing_account, fake_iam):
        """It is what tells the proxy whose published certificates to trust."""
        bz_token.mint(
            run_id="abc",
            agent="a",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by=None,
        )
        claims = json.loads(fake_iam["request"]["payload"])
        assert claims["iss"] == SERVICE_ACCOUNT

    def test_a_configured_account_alone_is_enough_to_mint(self, signing_account):
        """No private key set, yet minting is available."""
        assert bz_token.is_configured()


class TestLifetimeCeiling:
    def test_a_lifetime_past_the_iam_ceiling_is_refused(
        self, signing_account, fake_iam, monkeypatch
    ):
        """IAM will not sign past 12 hours; say so in terms of the job timeout."""
        monkeypatch.setattr(
            settings, "job_execution_timeout_seconds", 13 * 60 * 60, raising=False
        )
        with pytest.raises(RuntimeError, match="job_execution_timeout_seconds"):
            bz_token.mint(
                run_id="abc",
                agent="a",
                scope=PUBLIC_READ_SCOPE,
                inputs=Inputs(),
                requested_by=None,
            )

    def test_the_default_job_timeout_fits(self, signing_account, fake_iam):
        lifetime = (
            settings.job_execution_timeout_seconds + settings.bz_token_grace_seconds
        )
        assert lifetime <= bz_token.MAX_TOKEN_LIFETIME_SECONDS


class TestNotAGoogleCredential:
    """Signed by hackbot-api's own service account key.

    Only the claims keep one from also being a Google credential for that
    account, so these pin the claims that do the work.
    """

    def test_the_subject_is_the_run_not_the_signing_account(
        self, signing_account, fake_iam
    ):
        """Direct JWT auth to a Google API requires sub == iss."""
        bz_token.mint(
            run_id="abc",
            agent="a",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by=None,
        )
        claims = json.loads(fake_iam["request"]["payload"])
        assert claims["sub"] == "run:abc"
        assert claims["sub"] != claims["iss"]

    def test_there_is_no_scope_claim(self, signing_account, fake_iam):
        """The OAuth JWT-bearer exchange requires one to grant anything."""
        bz_token.mint(
            run_id="abc",
            agent="a",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by=None,
        )
        assert "scope" not in json.loads(fake_iam["request"]["payload"])

    def test_the_audience_is_not_a_google_endpoint(self, signing_account, fake_iam):
        bz_token.mint(
            run_id="abc",
            agent="a",
            scope=PUBLIC_READ_SCOPE,
            inputs=Inputs(),
            requested_by=None,
        )
        aud = json.loads(fake_iam["request"]["payload"])["aud"]
        assert aud == bz_token.TOKEN_AUDIENCE
        assert not aud.split("://")[-1].split("/")[0].lower().endswith("googleapis.com")

    def test_the_audience_cannot_be_reconfigured(self):
        """A constant, so no deploy can point it at Google.

        Were it a setting, `https://oauth2.googleapis.com/token` would turn a
        Bugzilla read token into an impersonation credential for this service.
        """
        assert bz_token.TOKEN_AUDIENCE == "hackbot-bugzilla-proxy"
        assert not hasattr(settings, "bz_token_audience")
        host = bz_token.TOKEN_AUDIENCE.split("://")[-1].split("/")[0].lower()
        assert not host.endswith("googleapis.com")
