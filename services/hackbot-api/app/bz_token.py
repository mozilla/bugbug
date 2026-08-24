"""Minting the per-run Bugzilla capability token.

Lets a run read Bugzilla through `bugzilla-proxy` without holding a BMO
credential. Built entirely from the agent's registered scope template: **a
caller never supplies a scope**.

Signing goes through IAM's `signJwt`, as this service's own identity, using a
Google-managed key. Nothing here holds or provisions key material, and the proxy
verifies against the certificates Google publishes for the same account, so the
two share one string (that account's email) and no keys are exchanged. A PEM
stands in for local runs.
"""

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from uuid import uuid4

from app.config import settings

log = logging.getLogger(__name__)

_JWT_HEADER = {"alg": "RS256", "typ": "JWT"}

# IAM refuses to sign a JWT more than 12 hours out. Comfortably past the 8 hour
# default job timeout, but a hard ceiling on one token's reach.
MAX_TOKEN_LIFETIME_SECONDS = 12 * 60 * 60


# The `bugzilla-proxy` service verifies against the same literal.
TOKEN_AUDIENCE = "hackbot-bugzilla-proxy"
LOCAL_ISSUER = "hackbot-api"


@dataclass(frozen=True)
class BugzillaScope:
    """What one agent's runs may read, before per-run substitution.

    On the registry entry rather than in the inputs, which is what keeps the
    scope server-determined. `grants` holds raw claims in the wire format the
    proxy parses; `"$bug_id"` in a `static_bugs` list becomes this run's bug.
    """

    grants: tuple[dict[str, Any], ...]
    attachments: bool = False
    filter_content: str = "off"
    confidential: bool = False
    promotions_max: int = 0

    def resolve(self, inputs: Any) -> dict[str, Any]:
        """Render the `bz` claim for one run."""
        bug_id = getattr(inputs, "bug_id", None)
        return {
            "read_only": True,
            "confidential": self.confidential,
            "attachments": self.attachments,
            "filter_content": self.filter_content,
            "promotions_max": self.promotions_max,
            "grants": [_substitute(grant, bug_id) for grant in self.grants],
        }


def _substitute(grant: dict[str, Any], bug_id: int | None) -> dict[str, Any]:
    """Replace `$bug_id` placeholders with this run's bug.

    Raises when the run has none: an empty allowlist would deny everything and
    a dropped rule would allow everything, so neither is a safe default.
    """
    resolved = json.loads(json.dumps(grant))
    anchor = resolved.get("anchor")
    if not isinstance(anchor, dict):
        return resolved
    static = anchor.get("static_bugs")
    if not isinstance(static, list) or "$bug_id" not in static:
        return resolved
    if bug_id is None:
        raise ValueError(
            "scope template references $bug_id but the run's inputs have none"
        )
    anchor["static_bugs"] = [
        int(bug_id) if entry == "$bug_id" else entry for entry in static
    ]
    return resolved


@lru_cache(maxsize=1)
def _iam_client():
    """The IAM Credentials client, built once and reused.

    A function rather than a module-level import, so the dependency is only
    touched on the signing path and tests can stub it.
    """
    from google.cloud import iam_credentials_v1

    return iam_credentials_v1.IAMCredentialsClient()


def _sign_with_service_account(claims: dict[str, Any]) -> str:
    """Have IAM sign these claims as this service's own identity.

    Needs `iam.serviceAccounts.signJwt` on the account being signed as, granted
    by `roles/iam.serviceAccountTokenCreator`, which this service already holds
    on itself for the GCS signed-policy path.
    """
    response = _iam_client().sign_jwt(
        request={
            "name": f"projects/-/serviceAccounts/{settings.bz_token_service_account}",
            "payload": json.dumps(claims, separators=(",", ":")),
        }
    )
    return response.signed_jwt


def _sign_locally(claims: dict[str, Any]) -> str:
    """Sign with a PEM from the environment, for local runs and tests."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    signing_input = ".".join(
        (
            _b64(json.dumps(_JWT_HEADER, separators=(",", ":")).encode()),
            _b64(json.dumps(claims, separators=(",", ":")).encode()),
        )
    ).encode()
    key = load_pem_private_key(settings.bz_token_private_key.encode(), password=None)
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input.decode()}.{_b64(signature)}"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def is_configured() -> bool:
    """True if this deployment can mint tokens at all.

    False is legitimate during rollout: the broker then falls back to its own
    Bugzilla credential, which is the way back if the proxy misbehaves.
    """
    return bool(settings.bz_token_service_account or settings.bz_token_private_key)


def mint(
    *,
    run_id: str,
    agent: str,
    scope: BugzillaScope,
    inputs: Any,
    requested_by: str | None,
) -> str:
    """Build and sign this run's token."""
    if not is_configured():
        raise RuntimeError("no signing key configured")

    lifetime = settings.job_execution_timeout_seconds + settings.bz_token_grace_seconds
    if lifetime > MAX_TOKEN_LIFETIME_SECONDS:
        # Caught here because IAM's rejection would not mention the job timeout.
        raise RuntimeError(
            f"a token would need to live {lifetime}s "
            f"(job_execution_timeout_seconds + bz_token_grace_seconds), but IAM "
            f"will not sign one past {MAX_TOKEN_LIFETIME_SECONDS}s"
        )

    now = datetime.now(timezone.utc)
    # Must outlive the job: a run that loses Bugzilla access partway through
    # fails for a reason that looks nothing like the cause.
    expires = now + timedelta(seconds=lifetime)

    claims = {
        # The proxy checks this, and it names whose certificates verify it.
        "iss": settings.bz_token_service_account or LOCAL_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "sub": f"run:{run_id}",
        "jti": uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "agent": agent,
        "requested_by": requested_by,
        "bz": scope.resolve(inputs),
    }

    if settings.bz_token_service_account:
        return _sign_with_service_account(claims)
    return _sign_locally(claims)


def broker_env_for(
    *,
    run_id: str,
    agent: str,
    scope: BugzillaScope | None,
    inputs: Any,
    requested_by: str | None,
) -> dict[str, str]:
    """The broker container's per-run environment, or empty.

    The only thing the API sets on `broker`; `jobs.trigger_execution` refuses
    anything outside its allowlist.
    """
    if scope is None or not is_configured():
        return {}
    token = mint(
        run_id=run_id,
        agent=agent,
        scope=scope,
        inputs=inputs,
        requested_by=requested_by,
    )
    return {"BUGZILLA_SCOPE_TOKEN": token}


# Convenience for registry entries: the shape phase 0 issues, where the proxy's
# upstream credential can see no more than the agents' own key could.
PUBLIC_READ_SCOPE = BugzillaScope(
    grants=(
        {
            "tier": "full",
            "anchor": {},
            "endpoints": ["bug", "bug/*/comment"],
        },
    ),
)

PUBLIC_READ_WITH_ATTACHMENTS = BugzillaScope(
    grants=(
        {
            "tier": "full",
            "anchor": {},
            "endpoints": [
                "bug",
                "bug/*/comment",
                "bug/*/attachment",
                "bug/attachment/*",
            ],
        },
    ),
    attachments=True,
)

__all__ = [
    "BugzillaScope",
    "PUBLIC_READ_SCOPE",
    "PUBLIC_READ_WITH_ATTACHMENTS",
    "broker_env_for",
    "is_configured",
    "mint",
]
