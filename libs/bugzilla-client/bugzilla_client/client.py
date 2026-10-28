"""Small shared Bugzilla REST client.

A minimal ``httpx``-based client for the handful of calls hackbot makes, in
place of the mix of ``requests``, raw ``httpx`` and ``bugsy`` used today. It
deliberately avoids ``libmozdata``'s bulk, futures-oriented client, which suits
the bugbug data pipelines but not single-bug reads and writes from an event
loop.

Config is injected: pass a :class:`BugzillaSettings`, or let the client load
one from the environment (via ``BugzillaSettings.from_env``) when none is
provided. The API is asynchronous.

The client reuses one connection pool across requests. Close it when done,
either with ``async with`` or by calling :meth:`BugzillaClient.close`::
"""

import base64
import logging
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import (
    RetryCallState,
    before_sleep_log,
    retry,
    stop_after_attempt,
    wait_exponential_jitter,
)

from bugzilla_client.config import BugzillaSettings

log = logging.getLogger(__name__)


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
    # Rate limiting and gateway failures: the request was not processed.
    if isinstance(exc, BugzillaError) and exc.status in {429, 502, 503, 504}:
        return is_read or exc.status == 429
    return False


class BugzillaClient:
    def __init__(self, settings: BugzillaSettings | None = None) -> None:
        self.settings = settings or BugzillaSettings.from_env()
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/rest/",
            headers=self.headers,
            timeout=self.settings.timeout_seconds,
        )
        # One resource per section from the REST API docs.
        self.bugs = _Bugs(self)
        self.comments = _Comments(self)
        self.attachments = _Attachments(self)
        self.users = _Users(self)

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @property
    def base_url(self) -> str:
        return self.settings.url.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        headers = {"X-Bugzilla-API-Key": self.settings.api_key}
        if self.settings.edge_key:
            headers["Mozilla-Edge-Key"] = self.settings.edge_key
        return headers

    def bug_url(self, bug_id: int) -> str:
        return f"{self.base_url}/show_bug.cgi?id={bug_id}"

    @retry(
        retry=_should_retry,
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(max=15),
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
        response = await self._http.request(method, path, params=params, json=json)
        return _parse(response)


class _Resource:
    def __init__(self, client: BugzillaClient) -> None:
        self._client = client


class _Bugs(_Resource):
    """Bugzilla REST API: https://bmo.readthedocs.io/en/latest/api/core/v1/bug.html."""

    async def get(
        self, ids: list[int], *, include_fields: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch bugs by id in one request.

        Bugs the API key cannot see are left out of the result rather than
        failing the request, so callers must not assume one bug per id.
        """
        if not ids:
            return []
        params: dict[str, Any] = {"id": ",".join(str(i) for i in ids)}
        if include_fields:
            params["include_fields"] = include_fields
        result = await self._client.request("GET", "bug", params=params)
        return result["bugs"]

    async def search(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Find bugs matching search criteria (``GET /bug``).

        ``params`` are Bugzilla search fields, e.g.
        ``{"product": "Core", "status": "NEW", "keywords": "regression"}``.
        Results are capped by the server (10,000 by default); page with
        ``limit`` and ``offset``. Use :meth:`get` when the ids are known.
        """
        result = await self._client.request("GET", "bug", params=params)
        return result["bugs"]

    async def create(self, fields: dict[str, Any]) -> int:
        """File a bug and return its id."""
        result = await self._client.request("POST", "bug", json=fields)
        return result["id"]

    async def update(
        self, bug_id: int, changes: dict[str, Any]
    ) -> dict[str, dict[str, str]]:
        """Apply field changes and/or a comment in one ``PUT /bug/{id}``.

        Bugzilla applies the whole body as a single transaction (one bugmail,
        one history entry). Returns what actually changed, by field name; empty
        when every field already had its value (a comment is not a change).

        Updates one bug only: do not pass ``ids`` in ``changes``.
        """
        result = await self._client.request("PUT", f"bug/{bug_id}", json=changes)
        (bug,) = result["bugs"]
        return bug["changes"]


class _Comments(_Resource):
    """Bugzilla REST API: https://bmo.readthedocs.io/en/latest/api/core/v1/comment.html."""

    async def get(self, comment_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Map each comment id to its comment, fetched in one request."""
        if not comment_ids:
            return {}
        # Repeated ``comment_ids=2&comment_ids=3``: Bugzilla rejects comma-joined ids.
        first, *rest = comment_ids
        params = {"comment_ids": rest} if rest else None
        result = await self._client.request(
            "GET", f"bug/comment/{first}", params=params
        )
        return {
            int(comment_id): comment
            for comment_id, comment in result["comments"].items()
        }

    async def get_for_bugs(self, bug_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
        """Map each bug id to its comments, fetched in one request."""
        if not bug_ids:
            return {}
        # Repeated ``ids=2&ids=3``: Bugzilla rejects comma-joined ids here.
        first, *rest = bug_ids
        params = {"ids": rest} if rest else None
        result = await self._client.request(
            "GET", f"bug/{first}/comment", params=params
        )
        return {
            int(bug_id): data["comments"] for bug_id, data in result["bugs"].items()
        }


class _Attachments(_Resource):
    """Bugzilla REST API: https://bmo.readthedocs.io/en/latest/api/core/v1/attachment.html."""

    async def get_for_bug(
        self, bug_id: int, *, include_data: bool = False
    ) -> list[dict[str, Any]]:
        """The attachments on a bug, without their content unless asked."""
        params = None if include_data else {"exclude_fields": "data"}
        result = await self._client.request(
            "GET", f"bug/{bug_id}/attachment", params=params
        )
        return result["bugs"][str(bug_id)]

    async def get(
        self, attachment_id: int, *, include_data: bool = False
    ) -> dict[str, Any] | None:
        """One attachment, or ``None`` if Bugzilla did not return it."""
        params = None if include_data else {"exclude_fields": "data"}
        result = await self._client.request(
            "GET", f"bug/attachment/{attachment_id}", params=params
        )
        # BMO answers an unknown id with an empty result, not an error.
        return result["attachments"].get(str(attachment_id))

    async def create(self, bug_id: int, data: bytes, fields: dict[str, Any]) -> int:
        """Attach a file to a bug and return the new attachment id.

        ``fields`` are the other Create Attachment fields, e.g. ``file_name``,
        ``summary``, ``content_type``, ``is_patch`` or ``comment``. ``data`` is
        the raw file content; it is base64-encoded here.
        """
        body = {
            **fields,
            "ids": [bug_id],
            "data": base64.b64encode(data).decode("ascii"),
        }
        result = await self._client.request(
            "POST", f"bug/{bug_id}/attachment", json=body
        )
        (attachment_id,) = result["attachments"]
        return int(attachment_id)


class _Users(_Resource):
    """Bugzilla REST API: https://bmo.readthedocs.io/en/latest/api/core/v1/user.html."""

    async def get(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Get users with raw ``GET /user`` query parameters."""
        result = await self._client.request("GET", "user", params=params)
        return result["users"]


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
            data["message"], code=data["code"], status=response.status_code
        )
    if response.is_error or not isinstance(data, dict):
        raise BugzillaError(
            f"Unexpected Bugzilla response: HTTP {response.status_code} "
            f"for {response.request.url}",
            status=response.status_code,
        )
    return data
