"""Publish run artifacts: upload via the signed policy, else write locally.

A single rule across the runtime — summary, logs, attachments: if an uploader
is configured the artifact is uploaded under ``key``; otherwise it is written
to ``artifacts_dir / key`` so local/compose/direct runs leave everything
retrievable on the host. Both branches use the same ``key``, so a downstream
apply step resolves it identically against GCS or the local dir.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from hackbot_runtime.uploader import SignedPolicyUploader


def publish_file(
    uploader: SignedPolicyUploader | None,
    artifacts_dir: Path | None,
    key: str,
    path: Path,
    content_type: str | None = None,
) -> str:
    if uploader is not None:
        uploader.upload_file(key, path, content_type)
    elif artifacts_dir is not None:
        dest = artifacts_dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    return key


def publish_bytes(
    uploader: SignedPolicyUploader | None,
    artifacts_dir: Path | None,
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    if uploader is not None:
        uploader.upload_bytes(key, data, content_type)
    elif artifacts_dir is not None:
        dest = artifacts_dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    return key


def publish_json(
    uploader: SignedPolicyUploader | None,
    artifacts_dir: Path | None,
    key: str,
    payload: dict,
) -> str:
    return publish_bytes(
        uploader,
        artifacts_dir,
        key,
        json.dumps(payload, indent=2, default=str).encode("utf-8"),
        "application/json",
    )
