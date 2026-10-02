"""Small shared Bugzilla REST client.

A minimal ``httpx``-based client for the handful of calls hackbot makes, in
place of the mix of ``requests``, raw ``httpx`` and ``bugsy`` used today. It
deliberately avoids ``libmozdata``'s bulk, futures-oriented client, which suits
the bugbug data pipelines but not single-bug reads and writes from an event
loop.

Config is injected: pass a :class:`BugzillaSettings`, or let the client load
one from the environment (via ``BugzillaSettings.from_env``) when none is
provided. The API is asynchronous.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx
from pydantic import BaseModel
from tenacity import (
    RetryCallState,
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
)

from bugzilla_client.config import BugzillaSettings
from bugzilla_client.models import (
    Attachment,
    Bug,
    BugUpdate,
    Comment,
    FieldChange,
    NewAttachment,
    NewBug,
)

log = logging.getLogger(__name__)

# Rate limiting and gateway failures: the request was not processed.
_RETRY_STATUSES = frozenset({429, 502, 503, 504})
_ATTEMPTS = 4
_MAX_WAIT_SECONDS = 15


class BugzillaError(Exception):
    """Bugzilla rejected a request, or answered with something that is not JSON.

    ``code`` is Bugzilla's own error code (e.g. 101 "bug does not exist", 102
    "access denied") when the response carried one.
    """

    def __init__(
        self, message: str, *, code: int | None = None, status: int | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _should_retry(state: RetryCallState) -> bool:
    """Tenacity's retry check for ``request``: is the failed call worth resending?"""
    exc = state.outcome.exception() if state.outcome else None
    if exc is None:
        return False
    # ``request(self, method, path, /, ...)``: ``method`` is always positional.
    is_read = state.args[1].upper() == "GET"
    return _is_transient(exc, is_read=is_read)


def _is_transient(exc: BaseException, *, is_read: bool) -> bool:
    """Whether a failed request is worth sending again.

    A write is only resent when Bugzilla surely did not apply it: a rate limit,
    or a connection that was never made. After a gateway error or a dropped
    response the write may have landed, and resending could post a comment
    twice.
    """
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    if isinstance(exc, httpx.TransportError):
        return is_read
    if isinstance(exc, BugzillaError) and exc.status in _RETRY_STATUSES:
        return is_read or exc.status == 429
    return False


class BugzillaClient:
    def __init__(self, settings: BugzillaSettings | None = None) -> None:
        self.settings = settings or BugzillaSettings.from_env()

    @property
    def base_url(self) -> str:
        return self.settings.url.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Bugzilla-API-Key": self.settings.api_key,
        }
        if self.settings.edge_key:
            headers["Mozilla-Edge-Key"] = self.settings.edge_key
        return headers

    def bug_url(self, bug_id: int) -> str:
        return f"{self.base_url}/show_bug.cgi?id={bug_id}"

    @retry(
        retry=_should_retry,
        stop=stop_after_attempt(_ATTEMPTS),
        wait=wait_exponential_jitter(max=_MAX_WAIT_SECONDS),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    async def request(
        self,
        method: str,
        path: str,
        /,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a REST endpoint (relative to ``/rest/``) and return its JSON body.

        Rate limits and gateway errors are retried with exponential backoff.
        Reads are also retried on network errors; writes only when the
        connection was never made, so a comment is never posted twice.

        Raises :class:`BugzillaError` for a Bugzilla-level error or a non-JSON
        answer (e.g. a misconfigured URL serving HTML).
        """
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
            response = await client.request(
                method,
                f"{self.base_url}/rest/{path.lstrip('/')}",
                params=params,
                json=json,
                headers=self.headers,
            )
        return _parse(response)

    # Reads

    async def get_bugs(
        self, ids: list[int], *, include_fields: str | None = None
    ) -> list[Bug]:
        """Fetch bugs by id in one request.

        Bugs the API key cannot see are left out of the result rather than
        failing the request, so callers must not assume one bug per id.
        """
        if not ids:
            return []
        params: dict[str, Any] = {"id": ",".join(str(i) for i in ids)}
        if include_fields:
            params["include_fields"] = include_fields
        return await self.search_bugs(params)

    async def search_bugs(self, params: dict[str, Any]) -> list[Bug]:
        """Search with raw ``GET /bug`` query parameters."""
        result = await self.request("GET", "bug", params=params)
        return [Bug.model_validate(bug) for bug in result.get("bugs", [])]

    async def get_comments(self, bug_ids: list[int]) -> dict[int, list[Comment]]:
        """Map each bug id to its comments, fetched in one request."""
        if not bug_ids:
            return {}
        # ``/bug/{first}/comment?ids=rest`` returns comments for every bug.
        first, *rest = bug_ids
        params = {"ids": ",".join(str(i) for i in rest)} if rest else None
        result = await self.request("GET", f"bug/{first}/comment", params=params)
        return {
            int(bug_id): [Comment.model_validate(c) for c in data["comments"]]
            for bug_id, data in result.get("bugs", {}).items()
        }

    async def get_attachments(
        self, bug_id: int, *, include_data: bool = False
    ) -> list[Attachment]:
        """The attachments on a bug, without their content unless asked."""
        params = None if include_data else {"exclude_fields": "data"}
        result = await self.request("GET", f"bug/{bug_id}/attachment", params=params)
        attachments = result["bugs"].get(str(bug_id), [])
        return [Attachment.model_validate(a) for a in attachments]

    async def get_attachment(
        self, attachment_id: int, *, include_data: bool = True
    ) -> Attachment | None:
        """One attachment, or ``None`` if Bugzilla did not return it."""
        params = None if include_data else {"exclude_fields": "data"}
        result = await self.request(
            "GET", f"bug/attachment/{attachment_id}", params=params
        )
        raw = result["attachments"].get(str(attachment_id))
        return Attachment.model_validate(raw) if raw is not None else None

    async def is_user_in_group(self, login: str, group: str) -> bool:
        """Whether a Bugzilla account exists and belongs to ``group``."""
        result = await self.request(
            "GET",
            "user",
            params={
                "names": login,
                "groups": group,
                "include_fields": "name",
                # Report an unknown login in ``faults`` instead of failing the
                # request, so it reads as "not in the group", not an error.
                "permissive": "1",
            },
        )
        return bool(result.get("users"))

    # Writes

    async def update_bug(
        self, bug_id: int, update: BugUpdate
    ) -> dict[str, FieldChange]:
        """Apply field changes and/or a comment in one ``PUT /bug/{id}``.

        Bugzilla applies the whole body as a single transaction (one bugmail,
        one history entry). Returns what actually changed, by field name; empty
        when every field already had its value (a comment is not a change).
        """
        result = await self.request("PUT", f"bug/{bug_id}", json=_body(update))
        (bug,) = result["bugs"]
        return {
            field: FieldChange.model_validate(change)
            for field, change in bug["changes"].items()
        }

    async def create_bug(self, bug: NewBug) -> int:
        """File a bug and return its id."""
        result = await self.request("POST", "bug", json=_body(bug))
        return result["id"]

    async def add_attachment(self, bug_id: int, attachment: NewAttachment) -> int:
        """Attach a file to a bug and return the new attachment id."""
        # ``data`` is left out of the dump: JSON can't hold raw (binary) bytes.
        body = attachment.model_dump(mode="json", exclude={"data"}, exclude_none=True)
        body["ids"] = [bug_id]
        body["data"] = base64.b64encode(attachment.data).decode("ascii")
        result = await self.request("POST", f"bug/{bug_id}/attachment", json=body)
        # BMO answers ``{"attachments": {"<id>": {...}}}``; upstream Bugzilla
        # answers ``{"ids": [...]}``. One bug was targeted, so expect one id.
        if "attachments" in result:
            ids = [int(a["id"]) for a in result["attachments"].values()]
        else:
            ids = [int(i) for i in result.get("ids") or []]
        if len(ids) != 1:
            raise BugzillaError(f"Expected one new attachment id, got {ids}")
        return ids[0]


def _body(model: BaseModel) -> dict[str, Any]:
    """The JSON body for a write model; unset fields are left out, not ``null``."""
    return model.model_dump(mode="json", exclude_none=True)


def _parse(response: httpx.Response) -> dict[str, Any]:
    """Return the JSON body, raising :class:`BugzillaError` for any error.

    Bugzilla reports errors as ``{"error": true, "code": ..., "message": ...}``;
    anything else that is not a JSON success (e.g. a proxy's HTML error page)
    is reported by its HTTP status.
    """
    try:
        data = response.json()
    except ValueError:
        data = None

    if isinstance(data, dict) and data.get("error"):
        raise BugzillaError(
            data.get("message") or "Unknown Bugzilla error",
            code=data.get("code"),
            status=response.status_code,
        )
    if response.is_error or not isinstance(data, dict):
        raise BugzillaError(
            f"Unexpected Bugzilla response: HTTP {response.status_code} "
            f"for {response.request.url}",
            status=response.status_code,
        )
    return data
