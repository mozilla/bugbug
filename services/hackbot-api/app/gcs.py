import asyncio
import base64
import binascii
import datetime
import json
import logging
from functools import lru_cache
from typing import Any

import google.auth
from google.auth import impersonated_credentials
from google.auth.transport.requests import Request as AuthRequest
from google.cloud import storage

from app.config import settings
from app.schemas import ArtifactRef, RunSummary

log = logging.getLogger(__name__)


def run_prefix(run_id: str) -> str:
    return f"runs/{run_id}/"


def summary_blob_name(run_id: str) -> str:
    return f"{run_prefix(run_id)}summary.json"


@lru_cache(maxsize=1)
def _signing_credentials() -> impersonated_credentials.Credentials:
    """Impersonate-self credentials so we can `sign_bytes` on Cloud Run.

    Cloud Run gives us `compute_engine.Credentials` (metadata-server
    token only — no local private key). Wrap them with
    `impersonated_credentials` targeting the same SA: that produces a
    `Signing` credential that delegates `sign_bytes` to the IAM
    `signBlob` API. The runtime SA needs `roles/iam.serviceAccountTokenCreator`
    on itself for the delegation to work.

    For local dev: `gcloud auth application-default login
    --impersonate-service-account=<sa>` produces an already-signing
    credential and this wrapper is a cheap no-op.
    """
    source, _ = google.auth.default()
    source.refresh(AuthRequest())
    sa_email = getattr(source, "service_account_email", None)
    if not sa_email:
        raise RuntimeError(
            "Default credentials don't expose a service_account_email. "
            "On Cloud Run this should be automatic; for local dev use "
            "`gcloud auth application-default login "
            "--impersonate-service-account=<sa>`."
        )
    return impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=sa_email,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )


@lru_cache(maxsize=1)
def _client() -> storage.Client:
    return storage.Client(
        project=settings.gcp_project or None, credentials=_signing_credentials()
    )


def _generate_post_policy_sync(run_id: str) -> dict[str, Any]:
    """Mint a V4 signed POST policy for uploads under `runs/<run_id>/`.

    `storage.Client.generate_signed_post_policy_v4` insists on adding an
    exact-match `{"key": blob_name}` condition (see client.py:1918),
    which contradicts the multi-artifact `starts-with` design. We build
    the policy manually so only `starts-with $key, prefix` constrains the
    blob name.
    """
    bucket_name = settings.results_bucket
    if not bucket_name:
        raise RuntimeError("results_bucket not configured")

    prefix = run_prefix(run_id)
    expiration_seconds = (
        settings.job_execution_timeout_seconds + settings.signed_policy_grace_seconds
    )

    creds = _signing_credentials()
    sa_email = creds.service_account_email

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    expires_at = now + datetime.timedelta(seconds=expiration_seconds)
    x_goog_credential = f"{sa_email}/{datestamp}/auto/storage/goog4_request"

    conditions: list[dict | list] = [
        {"bucket": bucket_name},
        ["starts-with", "$key", prefix],
        ["content-length-range", 0, settings.signed_policy_max_bytes],
        {"x-goog-date": timestamp},
        {"x-goog-credential": x_goog_credential},
        {"x-goog-algorithm": "GOOG4-RSA-SHA256"},
    ]
    policy_json = json.dumps(
        {
            "conditions": conditions,
            "expiration": expires_at.isoformat() + "Z",
        },
        separators=(",", ":"),
    )
    str_to_sign = base64.b64encode(policy_json.encode("utf-8"))
    signature_bytes = creds.sign_bytes(str_to_sign)
    signature = binascii.hexlify(signature_bytes).decode("utf-8")

    fields = {
        "x-goog-algorithm": "GOOG4-RSA-SHA256",
        "x-goog-credential": x_goog_credential,
        "x-goog-date": timestamp,
        "x-goog-signature": signature,
        "policy": str_to_sign.decode("utf-8"),
    }
    url = f"https://storage.googleapis.com/{bucket_name}/"
    return {"url": url, "fields": fields}


async def generate_results_policy(run_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_generate_post_policy_sync, run_id)


def _read_summary_sync(run_id: str) -> RunSummary | None:
    bucket = _client().bucket(settings.results_bucket)
    blob = bucket.blob(summary_blob_name(run_id))
    if not blob.exists():
        return None
    raw = blob.download_as_bytes()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("summary.json for run %s is not valid JSON", run_id)
        return RunSummary(status="error", error="summary.json is not valid JSON")
    return RunSummary.model_validate(data)


async def read_summary(run_id: str) -> RunSummary | None:
    return await asyncio.to_thread(_read_summary_sync, run_id)


def _download_artifact_bytes_sync(run_id: str, key: str) -> bytes:
    bucket = _client().bucket(settings.results_bucket)
    blob = bucket.blob(f"{run_prefix(run_id)}{key}")
    return blob.download_as_bytes()


async def download_artifact_bytes(run_id: str, key: str) -> bytes:
    """Fetch the raw bytes of one artifact under a run's prefix.

    Backs `hackbot_runtime.actions.handlers.base.ApplyContext.download_artifact`
    for the action-applier — handlers ask for an artifact by its recorded key
    (e.g. "attachments/0/file", "changes/changes.patch") without knowing GCS
    is behind it.
    """
    return await asyncio.to_thread(_download_artifact_bytes_sync, run_id, key)


def _list_artifacts_sync(run_id: str) -> list[ArtifactRef]:
    bucket = _client().bucket(settings.results_bucket)
    prefix = run_prefix(run_id)
    artifacts: list[ArtifactRef] = []
    for blob in _client().list_blobs(bucket, prefix=prefix):
        name = blob.name.removeprefix(prefix)
        if not name:
            continue
        artifacts.append(
            ArtifactRef(
                name=name,
                size=blob.size or 0,
                content_type=blob.content_type,
            )
        )
    return artifacts


async def list_artifacts(run_id: str) -> list[ArtifactRef]:
    return await asyncio.to_thread(_list_artifacts_sync, run_id)


def _generate_artifact_download_url_sync(
    run_id: str, artifact_name: str, expiration_seconds: int
) -> str | None:
    """Mint a V4 signed GET URL for a single artifact, or None if it's missing.

    Reuses the impersonated signing credentials (same `sign_bytes` path as the
    upload POST policy), so the browser can download the object directly from
    GCS without the bucket being public.
    """
    bucket = _client().bucket(settings.results_bucket)
    blob = bucket.blob(f"{run_prefix(run_id)}{artifact_name}")
    if not blob.exists():
        return None
    return blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(seconds=expiration_seconds),
        method="GET",
        credentials=_signing_credentials(),
    )


async def generate_artifact_download_url(
    run_id: str, artifact_name: str, expiration_seconds: int = 600
) -> str | None:
    return await asyncio.to_thread(
        _generate_artifact_download_url_sync,
        run_id,
        artifact_name,
        expiration_seconds,
    )
