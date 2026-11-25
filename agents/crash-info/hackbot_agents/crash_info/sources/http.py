"""Shared HTTP plumbing for the evidence sources: one client, retries on
transient failures, and an optional on-disk cache that makes evaluation reruns
fast and reproducible."""

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

Params = dict[str, Any] | list[tuple[str, str]] | None

# hg.mozilla.org's edge answers 406 for a minute or more when several
# investigations query it at once; like a 429, it passes after a longer wait.
_RATE_LIMITED = {406, 429}
_RETRY_STATUS = {*_RATE_LIMITED, 500, 502, 503, 504}
MAX_WAIT_S = 90.0


class SourceError(RuntimeError):
    """A source could not answer (network failure, HTTP error)."""


class NotFound(SourceError):
    """The requested bug, file or changeset does not exist."""


class LeakageBlocked(SourceError):
    """Answering would reveal information from after the as-of cutoff."""


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout_s: float = 30.0,
        cache_dir: Path | None = None,
        cache_ttl_s: int = 86_400,
        max_retries: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
            transport=transport,
        )
        self.cache_dir = cache_dir.expanduser() if cache_dir else None
        self.cache_ttl_s = cache_ttl_s
        self.max_retries = max_retries
        self.requests_made = 0

    async def get_json(
        self,
        url: str,
        params: Params = None,
        *,
        headers: dict | None = None,
        cache: bool = True,
    ) -> Any:
        text = await self.get_text(url, params, headers=headers, cache=cache)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise SourceError(f"{url} did not return JSON") from e

    async def get_text(
        self,
        url: str,
        params: Params = None,
        *,
        headers: dict | None = None,
        cache: bool = True,
    ) -> str:
        # Requests carrying credentials are never cached: their answers may be
        # restricted data that later anonymous runs must not see.
        use_cache = (
            cache and self.cache_dir is not None and not _has_credentials(headers)
        )
        key = _cache_key(url, params, headers)
        if use_cache and (cached := self._cache_read(key)) is not None:
            return cached
        for attempt in range(self.max_retries):
            last = attempt == self.max_retries - 1
            try:
                self.requests_made += 1
                response = await self._client.get(url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if last:
                    raise SourceError(f"GET {url} failed: {e}") from e
                await asyncio.sleep(2**attempt)
                continue
            if response.status_code == 404:
                raise NotFound(f"not found: {response.url}")
            if response.status_code in _RETRY_STATUS and not last:
                await asyncio.sleep(_wait(response, attempt))
                continue
            if response.status_code >= 400:
                raise SourceError(
                    f"HTTP {response.status_code} for {response.url}: {response.text[:200]}"
                )
            if use_cache:
                self._cache_write(key, response.text)
            return response.text
        raise SourceError(f"GET {url} failed after {self.max_retries} attempts")

    def _cache_path(self, key: str) -> Path:
        assert self.cache_dir is not None
        return self.cache_dir / key[:2] / f"{key}.txt"

    def _cache_read(self, key: str) -> str | None:
        path = self._cache_path(key)
        try:
            if time.time() - path.stat().st_mtime > self.cache_ttl_s:
                return None
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _cache_write(self, key: str, text: str) -> None:
        path = self._cache_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError:
            pass  # A cache that cannot be written only costs speed.

    async def aclose(self) -> None:
        await self._client.aclose()


def _wait(response: httpx.Response, attempt: int) -> float:
    """Seconds before the next attempt: the server's Retry-After when it gives
    one; 5, 20, 80 s after a rate limit; 2, 4, 8 s after a server error."""
    retry_after = response.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return min(float(retry_after), MAX_WAIT_S)
    if response.status_code in _RATE_LIMITED:
        return min(5.0 * 4**attempt, MAX_WAIT_S)
    return 2.0 ** (attempt + 1)


def _has_credentials(headers: dict | None) -> bool:
    return any(
        "key" in k.lower() or k.lower() == "authorization" for k in headers or {}
    )


def _cache_key(url: str, params: Params, headers: dict | None) -> str:
    accept = (headers or {}).get("Accept", "")
    material = json.dumps([url, params or [], accept], sort_keys=True, default=str)
    return hashlib.sha256(material.encode()).hexdigest()
