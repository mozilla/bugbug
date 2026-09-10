"""Token verification and the claim shapes it refuses."""

import jwt
import pytest
from bugzilla_proxy.tokens import (
    CERTS_URL_TEMPLATE,
    TOKEN_AUDIENCE,
    TokenError,
    TokenVerifier,
    parse_scope,
)


def claims(bz: dict, **overrides) -> dict:
    base = {
        "sub": "run:abc",
        "jti": "t1",
        "agent": "frontend-triage",
        "requested_by": "someone@mozilla.com",
        "bz": bz,
    }
    base.update(overrides)
    return base


PUBLIC_GRANT = {"tier": "full", "anchor": {}, "endpoints": ["bug"]}


class TestParseScope:
    def test_a_public_scope_parses(self):
        scope = parse_scope(claims({"grants": [PUBLIC_GRANT]}))
        assert scope.run_id == "abc"
        assert scope.agent == "frontend-triage"
        assert not scope.is_private
        assert not scope.confidential

    def test_sub_must_name_a_run(self):
        with pytest.raises(TokenError, match="run:"):
            parse_scope(claims({"grants": [PUBLIC_GRANT]}, sub="someone"))

    def test_jti_is_required(self):
        with pytest.raises(TokenError, match="jti"):
            parse_scope(claims({"grants": [PUBLIC_GRANT]}, jti=""))

    def test_grants_must_be_present(self):
        with pytest.raises(TokenError, match="grants"):
            parse_scope(claims({"grants": []}))

    def test_writes_are_refused(self):
        with pytest.raises(TokenError, match="reads only"):
            parse_scope(claims({"read_only": False, "grants": [PUBLIC_GRANT]}))

    def test_an_unimplemented_filter_mode_is_refused_rather_than_ignored(self):
        """Serving unfiltered content because we cannot filter is the bad outcome."""
        with pytest.raises(TokenError, match="not implemented"):
            parse_scope(claims({"filter_content": "llm", "grants": [PUBLIC_GRANT]}))

    def test_unknown_endpoint_patterns_are_refused(self):
        grant = {"tier": "full", "anchor": {}, "endpoints": ["*"]}
        with pytest.raises(TokenError, match="unknown endpoint"):
            parse_scope(claims({"grants": [grant]}))

    def test_a_grant_needs_at_least_one_endpoint(self):
        grant = {"tier": "full", "anchor": {}, "endpoints": []}
        with pytest.raises(TokenError, match="at least one endpoint"):
            parse_scope(claims({"grants": [grant]}))

    def test_an_unknown_tier_is_refused(self):
        grant = {"tier": "everything", "anchor": {}, "endpoints": ["bug"]}
        with pytest.raises(TokenError, match="unknown tier"):
            parse_scope(claims({"grants": [grant]}))


class TestPrivateGrantGuards:
    def private_grant(self, anchor: dict) -> dict:
        return {"tier": "full", "anchor": anchor, "endpoints": ["bug"]}

    def test_a_private_grant_needs_a_structural_rule(self):
        grant = self.private_grant(
            {"groups": ["core-security"], "whiteboard": ["[sec-triage]"]}
        )
        with pytest.raises(TokenError, match="structural rule"):
            parse_scope(claims({"confidential": True, "grants": [grant]}))

    def test_a_narrowing_rule_alone_is_not_enough(self):
        grant = self.private_grant(
            {"groups": ["core-security"], "keywords": ["sec-high"]}
        )
        with pytest.raises(TokenError, match="structural rule"):
            parse_scope(claims({"confidential": True, "grants": [grant]}))

    def test_a_structural_rule_satisfies_the_guard(self):
        grant = self.private_grant(
            {
                "groups": ["core-security"],
                "product": ["Core"],
                "keywords": ["sec-high"],
            }
        )
        scope = parse_scope(claims({"confidential": True, "grants": [grant]}))
        assert scope.is_private

    def test_static_bugs_counts_as_structural(self):
        grant = self.private_grant(
            {"groups": ["core-security"], "static_bugs": [1899123]}
        )
        scope = parse_scope(claims({"confidential": True, "grants": [grant]}))
        assert scope.is_private

    def test_a_private_grant_requires_a_confidential_run(self):
        grant = self.private_grant(
            {"groups": ["core-security"], "static_bugs": [1899123]}
        )
        with pytest.raises(TokenError, match="confidential"):
            parse_scope(claims({"confidential": False, "grants": [grant]}))


class TestVerifier:
    def test_a_well_formed_token_verifies(self, settings, mint, public_scope):
        scope = TokenVerifier(settings).verify(mint(public_scope))
        assert scope.agent == "frontend-triage"
        assert scope.requested_by == "someone@mozilla.com"

    def test_no_token_is_refused(self, settings):
        with pytest.raises(TokenError, match="no token"):
            TokenVerifier(settings).verify("")

    def test_a_token_signed_by_someone_else_is_refused(
        self, settings, public_scope, keypair
    ):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = other.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode()
        forged = jwt.encode(
            {
                "iss": settings.token_issuer,
                "aud": TOKEN_AUDIENCE,
                "sub": "run:abc",
                "jti": "t1",
                "iat": 1_700_000_000,
                "exp": 4_000_000_000,
                "bz": public_scope,
            },
            pem,
            algorithm="RS256",
        )
        with pytest.raises(TokenError, match="not valid"):
            TokenVerifier(settings).verify(forged)

    def test_an_expired_token_is_refused(self, settings, mint, public_scope):
        expired = mint(public_scope, exp=1_700_000_001, iat=1_700_000_000)
        with pytest.raises(TokenError, match="not valid"):
            TokenVerifier(settings).verify(expired)

    def test_a_token_for_another_audience_is_refused(
        self, settings, mint, public_scope
    ):
        wrong = mint(public_scope, aud="some-other-service")
        with pytest.raises(TokenError, match="not valid"):
            TokenVerifier(settings).verify(wrong)

    def test_exactly_one_key_source_must_be_configured(self, settings):
        settings.token_issuer = "hackbot-api@p.iam.gserviceaccount.com"
        with pytest.raises(ValueError, match="exactly one"):
            TokenVerifier(settings)

    def test_no_key_source_at_all_is_refused(self, settings):
        settings.jwt_public_key = ""
        settings.token_issuer = ""
        with pytest.raises(ValueError, match="exactly one"):
            TokenVerifier(settings)


class TestServiceAccountVerification:
    """The deployed path, where no key material is configured on either side.

    Covers what replaces it: fetching the right account's certs, picking the
    key by id, and binding the issuer.
    """

    def test_a_token_signed_by_the_issuer_verifies(
        self, sa_settings, mint_sa, public_scope, served_certs
    ):
        scope = TokenVerifier(sa_settings).verify(mint_sa(public_scope))
        assert scope.agent == "frontend-triage"

    def test_the_certs_are_fetched_from_the_issuers_own_url(
        self, sa_settings, mint_sa, public_scope, served_certs
    ):
        """An issuer-specific URL is itself the binding to that issuer."""
        TokenVerifier(sa_settings).verify(mint_sa(public_scope))
        assert served_certs["calls"] == [
            "https://www.googleapis.com/robot/v1/metadata/x509/"
            "hackbot-api@example-project.iam.gserviceaccount.com"
        ]

    def test_certs_are_cached_across_requests(
        self, sa_settings, mint_sa, public_scope, served_certs
    ):
        verifier = TokenVerifier(sa_settings)
        verifier.verify(mint_sa(public_scope))
        verifier.verify(mint_sa(public_scope))
        assert len(served_certs["calls"]) == 1

    def test_a_token_from_another_issuer_is_refused(
        self, sa_settings, mint_sa, public_scope, served_certs
    ):
        wrong = mint_sa(public_scope, iss="someone-else@evil.iam.gserviceaccount.com")
        with pytest.raises(TokenError, match="not valid"):
            TokenVerifier(sa_settings).verify(wrong)

    def test_a_token_with_no_key_id_is_refused(
        self, sa_settings, mint, public_scope, served_certs
    ):
        with pytest.raises(TokenError, match="no key id"):
            TokenVerifier(sa_settings).verify(mint(public_scope))

    def test_an_unknown_key_id_triggers_one_refetch(
        self, sa_settings, mint_sa, public_scope, served_certs, signing_cert
    ):
        """Google rotates keys, so a miss is worth one refetch before failing."""
        verifier = TokenVerifier(sa_settings)
        verifier.verify(mint_sa(public_scope))
        served_certs["certs"] = {"rotated": signing_cert}

        scope = verifier.verify(mint_sa(public_scope, kid="rotated"))
        assert scope.agent == "frontend-triage"
        assert len(served_certs["calls"]) == 2

    def test_a_key_id_that_is_still_unknown_after_refetch_is_refused(
        self, sa_settings, mint_sa, public_scope, served_certs
    ):
        with pytest.raises(TokenError, match="unknown signing key"):
            TokenVerifier(sa_settings).verify(mint_sa(public_scope, kid="nope"))

    def test_a_stale_cache_survives_a_fetch_failure(
        self, sa_settings, mint_sa, public_scope, served_certs
    ):
        """Better slightly stale keys than every run losing Bugzilla at once."""
        verifier = TokenVerifier(sa_settings)
        verifier.verify(mint_sa(public_scope))
        verifier._keys._expires_at = 0.0
        served_certs["fail"] = True

        assert verifier.verify(mint_sa(public_scope)).agent == "frontend-triage"

    def test_a_fetch_failure_with_no_cache_is_refused(
        self, sa_settings, mint_sa, public_scope, served_certs
    ):
        served_certs["fail"] = True
        with pytest.raises(TokenError, match="cannot fetch"):
            TokenVerifier(sa_settings).verify(mint_sa(public_scope))


class TestCrossServiceContract:
    """The audience is one literal duplicated in two packages.

    They cannot import each other, so drift would pass every unit test here
    and reject every token in production.
    """

    def _hackbot_api_constant(self, name: str) -> str | None:
        import ast
        import pathlib

        source = (
            pathlib.Path(__file__).resolve().parents[2]
            / "hackbot-api"
            / "app"
            / "bz_token.py"
        )
        if not source.exists():
            pytest.skip("hackbot-api is not checked out beside this service")
        tree = ast.parse(source.read_text())
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
        return None

    def test_the_audience_matches_what_hackbot_api_mints(self):
        assert self._hackbot_api_constant("TOKEN_AUDIENCE") == TOKEN_AUDIENCE

    def test_the_audience_is_not_a_google_endpoint(self):
        """Google would accept such a token as a credential for the signer."""
        host = TOKEN_AUDIENCE.split("://")[-1].split("/")[0].lower()
        assert host != "googleapis.com"
        assert not host.endswith(".googleapis.com")

    def test_the_certs_url_is_googles_over_https(self):
        """The URL is pinned, not merely defaulted.

        Any other host, or plain HTTP, hands the choice of trusted keys to
        whoever answers the request.
        """
        assert CERTS_URL_TEMPLATE.startswith("https://www.googleapis.com/")
        assert "{service_account}" in CERTS_URL_TEMPLATE

    def test_the_certs_url_is_scoped_to_one_account(self):
        """A shared endpoint would let any Google account's key verify."""
        rendered = CERTS_URL_TEMPLATE.format(service_account="someone@example.com")
        assert rendered.endswith("/someone@example.com")
