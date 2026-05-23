import asyncio
import json
import logging
from datetime import timedelta
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
def _client() -> storage.Client:
    """Storage client whose credentials can sign blobs.

    Cloud Run gives us `compute_engine.Credentials` (metadata-server
    token only — no local private key), which the GCS library refuses
    to use for signing. Wrap it with `impersonated_credentials`
    targeting the same SA: that produces a `Signing` credential that
    delegates `sign_bytes` to the IAM `signBlob` API. The runtime SA
    needs `roles/iam.serviceAccountTokenCreator` on itself for the
    delegation to work.

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
    signing_creds = impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=sa_email,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return storage.Client(
        project=settings.gcp_project or None, credentials=signing_creds
    )


def _generate_post_policy_sync(run_id: str) -> dict[str, Any]:
    bucket_name = settings.results_bucket
    if not bucket_name:
        raise RuntimeError("results_bucket not configured")

    prefix = run_prefix(run_id)
    expiration_seconds = (
        settings.job_execution_timeout_seconds + settings.signed_policy_grace_seconds
    )

    policy = _client().generate_signed_post_policy_v4(
        bucket_name=bucket_name,
        blob_name=f"{prefix}_placeholder",
        expiration=timedelta(seconds=expiration_seconds),
        conditions=[
            ["starts-with", "$key", prefix],
            ["content-length-range", 0, settings.signed_policy_max_bytes],
        ],
    )
    return {"url": policy["url"], "fields": policy["fields"]}


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
