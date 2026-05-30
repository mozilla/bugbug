import json
from pathlib import Path

import requests


class SignedPolicyUploader:
    """POST artifacts to a GCS V4 signed POST policy.

    The orchestrator passes the policy via env vars consumed by `Context`.
    The Job has no GCP identity; this signed policy is its only write
    capability.
    """

    def __init__(self, url: str, fields: dict[str, str], prefix: str) -> None:
        self.url = url
        self.fields = fields
        self.prefix = prefix

    def upload_bytes(
        self, name: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        key = f"{self.prefix}{name}"
        form: dict[str, str] = dict(self.fields)
        form["key"] = key
        files = {"file": (name, data, content_type)}
        response = requests.post(self.url, data=form, files=files, timeout=300)
        if not response.ok:
            # GCS V4 signed POST policy errors are only readable in the
            # response body. `raise_for_status()` strips it, leaving an
            # unhelpful "400 Bad Request" — so include the body in the
            # exception message.
            raise requests.HTTPError(
                f"{response.status_code} uploading {name} to {self.url} "
                f"(key={key}, size={len(data)}): {response.text[:2000]}",
                response=response,
            )

    def upload_file(
        self, name: str, path: Path, content_type: str | None = None
    ) -> None:
        data = path.read_bytes()
        self.upload_bytes(name, data, content_type or "application/octet-stream")

    def upload_json(self, name: str, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.upload_bytes(name, body, "application/json")
