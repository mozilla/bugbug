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
either with ``async with`` or by calling :meth:`BugzillaClient.close`.
"""

import base64
import logging
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True)
class BugzillaResponse:
    """What a resource method returns; ``data`` is Bugzilla's JSON body.

    A wrapper rather than the bare body, so typed results can be added later
    without breaking callers.
    """

    data: dict[str, Any]


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
        self._settings = settings or BugzillaSettings.from_env()
        headers = {
            "X-Bugzilla-API-Key": self._settings.api_key,
            "User-Agent": self._settings.user_agent,
        }
        if self._settings.edge_key:
            headers["Mozilla-Edge-Key"] = self._settings.edge_key
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/rest/",
            headers=headers,
            timeout=self._settings.timeout_seconds,
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
        return self._settings.url.rstrip("/")

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
        self, ids: Collection[int], *, include_fields: Collection[str] | None = None
    ) -> BugzillaResponse:
        """Fetch bugs by id in one request.

        Bugs the API key cannot see are left out of the result rather than
        failing the request, so callers must not assume one bug per id.
        """
        if not ids:
            raise ValueError("ids must not be empty")
        params: dict[str, Any] = {"id": ",".join(str(i) for i in ids)}
        if include_fields:
            params["include_fields"] = ",".join(include_fields)
        result = await self._client.request("GET", "bug", params=params)
        return BugzillaResponse(result)

    async def search(
        self,
        params: dict[str, Any],
        *,
        include_fields: Collection[str] | None = None,
    ) -> BugzillaResponse:
        """Find bugs matching search criteria (``GET /bug``).

        ``params`` are Bugzilla search fields, e.g.
        ``{"product": "Core", "status": "NEW", "keywords": "regression"}``.
        Results are capped by the server (10,000 by default); page with
        ``limit`` and ``offset``. Use :meth:`get` when the ids are known.
        ``include_fields`` overrides the same key in ``params``.
        """
        if include_fields:
            params = {**params, "include_fields": ",".join(include_fields)}
        result = await self._client.request("GET", "bug", params=params)
        return BugzillaResponse(result)

    async def create(self, fields: dict[str, Any]) -> BugzillaResponse:
        """File a bug; Bugzilla answers ``{"id": ...}``."""
        result = await self._client.request("POST", "bug", json=fields)
        return BugzillaResponse(result)

    async def update(self, bug_id: int, changes: dict[str, Any]) -> BugzillaResponse:
        """Apply field changes and/or a comment in one ``PUT /bug/{id}``.

        Bugzilla applies the whole body as a single transaction (one bugmail,
        one history entry). Each entry in the answer's ``bugs`` lists what
        actually changed in ``changes``; empty when every field already had
        its value (a comment is not a change).

        Updates one bug only: do not pass ``ids`` in ``changes``.
        """
        result = await self._client.request("PUT", f"bug/{bug_id}", json=changes)
        return BugzillaResponse(result)


class _Comments(_Resource):
    """Bugzilla REST API: https://bmo.readthedocs.io/en/latest/api/core/v1/comment.html."""

    async def get(
        self,
        *,
        bug_ids: Collection[int] = (),
        comment_ids: Collection[int] = (),
        new_since: datetime | None = None,
        include_fields: Collection[str] | None = None,
        skip_private: bool = False,
    ) -> BugzillaResponse:
        """Comments by bug and/or by comment id, fetched in one request.

        Bugzilla answers ``{"bugs": {bug_id: {"comments": [...]}}, "comments":
        {comment_id: comment}}``, keyed by id strings. ``new_since`` only filters the
        comments of ``bug_ids``; ``skip_private`` skips bugs the API key cannot
        see instead of failing.
        """
        if not bug_ids and not comment_ids:
            raise ValueError("bug_ids or comment_ids must not be empty")
        # The path takes one id, the rest go in the query. Ids are repeated
        # (``ids=2&ids=3``): Bugzilla rejects comma-joined ids here.
        params: dict[str, Any]
        if bug_ids:
            first, *rest = bug_ids
            path = f"bug/{first}/comment"
            params = {"ids": rest, "comment_ids": list(comment_ids)}
        else:
            first, *rest = comment_ids
            path = f"bug/comment/{first}"
            params = {"comment_ids": rest}
        if new_since:
            params["new_since"] = new_since.isoformat()
        if include_fields:
            params["include_fields"] = ",".join(include_fields)
        if skip_private:
            params["skip_private"] = 1
        result = await self._client.request("GET", path, params=params)
        return BugzillaResponse(result)


class _Attachments(_Resource):
    """Bugzilla REST API: https://bmo.readthedocs.io/en/latest/api/core/v1/attachment.html."""

    async def get(
        self,
        *,
        bug_ids: Collection[int] = (),
        attachment_ids: Collection[int] = (),
        include_data: bool = False,
    ) -> BugzillaResponse:
        """Attachments by bug and/or by attachment id, fetched in one request.

        Bugzilla answers ``{"bugs": {bug_id: [attachment, ...]}, "attachments":
        {attachment_id: attachment}}``, keyed by id strings, without the
        file content unless ``include_data``. BMO leaves unknown attachment
        ids out of the result rather than failing.
        """
        if not bug_ids and not attachment_ids:
            raise ValueError("bug_ids or attachment_ids must not be empty")
        # The path takes one id, the rest go in the query. Ids are repeated
        # (``ids=2&ids=3``): Bugzilla rejects comma-joined ids here.
        if bug_ids:
            first, *rest = bug_ids
            path = f"bug/{first}/attachment"
            params: dict[str, Any] = {
                "ids": rest,
                "attachment_ids": list(attachment_ids),
            }
        else:
            first, *rest = attachment_ids
            path = f"bug/attachment/{first}"
            params = {"attachment_ids": rest}
        if not include_data:
            params["exclude_fields"] = "data"
        result = await self._client.request("GET", path, params=params)
        return BugzillaResponse(result)

    async def create(
        self, bug_id: int, data: bytes, fields: dict[str, Any]
    ) -> BugzillaResponse:
        """Attach a file to a bug.

        Bugzilla answers ``{"attachments": {attachment_id: attachment}}``.

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
        return BugzillaResponse(result)


class _Users(_Resource):
    """Bugzilla REST API: https://bmo.readthedocs.io/en/latest/api/core/v1/user.html."""

    async def get(
        self,
        *,
        ids: Collection[int] = (),
        names: Collection[str] = (),
        match: Collection[str] = (),
        limit: int | None = None,
        group_ids: Collection[int] = (),
        groups: Collection[str] = (),
        include_disabled: bool = False,
        permissive: bool = False,
    ) -> BugzillaResponse:
        """Users by id, login name, or a ``match`` search.

        ``limit`` and ``include_disabled`` only apply to ``match``;
        ``permissive`` reports unknown ``names`` in ``faults`` instead of
        failing. ``group_ids`` and ``groups`` keep only members of any of them.
        """
        if not ids and not names and not match:
            raise ValueError("ids, names or match must not be empty")
        # Lists are repeated (``names=a&names=b``): Bugzilla rejects commas here.
        params: dict[str, Any] = {
            "ids": list(ids),
            "names": list(names),
            "match": list(match),
            "group_ids": list(group_ids),
            "groups": list(groups),
        }
        if limit is not None:
            params["limit"] = limit
        if include_disabled:
            params["include_disabled"] = 1
        if permissive:
            params["permissive"] = 1
        result = await self._client.request("GET", "user", params=params)
        return BugzillaResponse(result)


def _parse(response: httpx.Response) -> dict[str, Any]:
    """Return the JSON body, raising :class:`BugzillaError` for any error.

    Bugzilla reports errors as ``{"error": true, "code": ..., "message": ...}``;
    anything else that is not a JSON success (e.g. a proxy's HTML error page)
    is reported by its HTTP status.
    """
    unexpected = (
        f"Unexpected Bugzilla response: HTTP {response.status_code} "
        f"for {response.request.url}"
    )
    try:
        data = response.json()
    except ValueError as exc:
        raise BugzillaError(unexpected, status=response.status_code) from exc

    if isinstance(data, dict) and data.get("error"):
        raise BugzillaError(
            data["message"], code=data["code"], status=response.status_code
        )
    if response.is_error or not isinstance(data, dict):
        raise BugzillaError(unexpected, status=response.status_code)
    return data
