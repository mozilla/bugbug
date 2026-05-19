import asyncio
import json
import logging
from datetime import timedelta
from functools import lru_cache
from typing import Any

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
    return storage.Client(project=settings.gcp_project or None)


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
