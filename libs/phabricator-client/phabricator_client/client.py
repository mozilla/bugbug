"""Small shared Phabricator Conduit client.

A minimal ``httpx``-based Conduit client, deliberately avoiding ``libmozdata``'s
heavier, bulk/futures-oriented client for the handful of lightweight calls
hackbot makes. Shared by the apply-side patch submitter (``hackbot_runtime``)
and the webhook receiver (``hackbot-api``) so there is a single Conduit
implementation.

Config is injected: pass a :class:`PhabricatorSettings`, or let the client load
one from the environment (via ``PhabricatorSettings.from_env``) when none is
provided. The API is synchronous; async callers should run it in a threadpool.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from phabricator_client.config import PhabricatorSettings


class PhabricatorClient:
    def __init__(self, settings: PhabricatorSettings | None = None) -> None:
        self.settings = settings or PhabricatorSettings.from_env()

    @property
    def base_url(self) -> str:
        return self.settings.url.rstrip("/")

    def revision_url(self, revision_id: int) -> str:
        return f"{self.base_url}/D{revision_id}"

    def conduit_request(self, method: str, **payload: Any) -> dict:
        """Call a Conduit method, returning its ``result`` (raising on error)."""
        payload["__conduit__"] = {"token": self.settings.api_key}
        response = httpx.post(
            f"{self.base_url}/api/{method}",
            data={"params": json.dumps(payload), "output": "json"},
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("error_code"):
            raise RuntimeError(
                f"Conduit error {data['error_code']}: {data.get('error_info')}"
            )
        return data["result"]

    def search_transactions(self, object_phid: str) -> list[dict]:
        """Return the transactions (comments, status changes, ...) on an object."""
        result = self.conduit_request(
            "transaction.search", objectIdentifier=object_phid
        )
        return result.get("data") or []

    def search_revision(self, revision_phid: str) -> dict | None:
        """Return the Differential revision for a PHID, or ``None`` if not found."""
        result = self.conduit_request(
            "differential.revision.search", constraints={"phids": [revision_phid]}
        )
        data = result.get("data") or []
        return data[0] if data else None
