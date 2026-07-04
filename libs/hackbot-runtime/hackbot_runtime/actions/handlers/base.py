from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


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
    download_artifact: Callable[[str], Awaitable[bytes]]
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
