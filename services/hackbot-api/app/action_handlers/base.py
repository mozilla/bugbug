from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol


class ArtifactTooLargeError(Exception):
    """An artifact is bigger than the caller is willing to download."""

    def __init__(self, key: str, size: int, max_bytes: int) -> None:
        super().__init__(
            f"Artifact {key} is {size / 1024 / 1024:.1f} MiB, over the "
            f"{max_bytes / 1024 / 1024:.1f} MiB limit"
        )
        self.key = key
        self.size = size
        self.max_bytes = max_bytes


class ArtifactDownloader(Protocol):
    """Fetches one of the run's artifacts by its recorded key.

    Raises :class:`ArtifactTooLargeError` instead of downloading an artifact
    bigger than ``max_bytes``, so an oversized artifact is never loaded into
    memory. Without ``max_bytes`` a default limit applies; a handler passes its
    own to change it.
    """

    def __call__(self, key: str, max_bytes: int | None = None) -> Awaitable[bytes]: ...


@dataclass
class ApplyContext:
    """Everything an :class:`ActionHandler` needs from the run.

    Scoped to a single action application (not the whole run), since only
    ``attachments`` varies per action. Handlers never talk to GCS directly —
    ``download_artifact`` is provided by the caller (hackbot-api's
    ``/internal/events/apply-run-actions`` route) — so this package stays free of
    a dependency on any particular storage backend. Async, matching
    hackbot-api's own GCS wrappers and ``ActionHandler.apply`` itself.
    """

    run_id: str
    agent: str
    download_artifact: ArtifactDownloader
    attachments: list[dict[str, str]] = field(default_factory=list)

    def artifact_key(self, name: str) -> str | None:
        """The uploaded key for an attachment recorded under ``name``, if any."""
        for attachment in self.attachments:
            if attachment.get("name") == name:
                return attachment.get("uploaded_key")
        return None


@dataclass
class ActionResult:
    status: str  # "applied" | "failed"
    result: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def ok(cls, result: dict[str, Any] | None = None) -> ActionResult:
        return cls(status="applied", result=result)

    @classmethod
    def failed(cls, error: str) -> ActionResult:
        return cls(status="failed", error=error)


class ActionHandler(Protocol):
    async def apply(
        self, params: dict[str, Any], ctx: ApplyContext
    ) -> ActionResult: ...
