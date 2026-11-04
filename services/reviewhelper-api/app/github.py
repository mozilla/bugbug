"""GitHub App authentication and API transport."""

import asyncio
import time
from datetime import datetime
from functools import cache

import httpx
import jwt

from app.config import settings
from bugbug.tools.core.connection import get_http_client


class GitHubApp:
    def __init__(self) -> None:
        self._tokens: dict[int, tuple[str, float]] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._installations: dict[str, int] = {}
        self._slug: str | None = None

    def jwt(self) -> str:
        if not settings.github_app_id or not settings.github_app_private_key:
            raise ValueError("GitHub App credentials are not configured")
        now = int(time.time())
        return jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": settings.github_app_id},
            settings.github_app_private_key.replace("\\n", "\n"),
            algorithm="RS256",
        )

    async def app_request(self, path: str) -> dict:
        response = await get_http_client().get(
            f"https://api.github.com/{path}",
            headers={
                "Authorization": f"Bearer {self.jwt()}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    async def bot_login(self) -> str:
        if self._slug is None:
            self._slug = (await self.app_request("app"))["slug"]
        return f"{self._slug}[bot]"

    async def token(self, installation_id: int) -> str:
        lock = self._locks.setdefault(installation_id, asyncio.Lock())
        async with lock:
            cached = self._tokens.get(installation_id)
            if cached and cached[1] > time.time() + 60:
                return cached[0]
            response = await get_http_client().post(
                f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                headers={
                    "Authorization": f"Bearer {self.jwt()}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=30,
            )
            response.raise_for_status()
            body = response.json()
            expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
            self._tokens[installation_id] = (body["token"], expires.timestamp())
            return body["token"]

    async def installation_id(self, repo: str) -> int:
        if repo not in self._installations:
            installation = await self.app_request(f"repos/{repo}/installation")
            self._installations[repo] = installation["id"]
        return self._installations[repo]

    def invalidate(self, installation_id: int) -> None:
        self._tokens.pop(installation_id, None)
        self._installations = {
            repo: installation
            for repo, installation in self._installations.items()
            if installation != installation_id
        }

    def client(self, installation_id: int) -> "GitHubClient":
        return GitHubClient(self, installation_id)

    async def repo_client(self, repo: str) -> "GitHubClient":
        return self.client(await self.installation_id(repo))


class GitHubClient:
    """Installation-scoped client; writes are reconciled by callers, never retried."""

    def __init__(self, app: GitHubApp, installation_id: int):
        self.app = app
        self.installation_id = installation_id

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if path.startswith("/") or "://" in path:
            raise ValueError("Expected a GitHub API-relative path")
        supplied_headers = kwargs.pop("headers", {})
        for attempt in range(3):
            token = await self.app.token(self.installation_id)
            try:
                response = await get_http_client().request(
                    method,
                    f"https://api.github.com/{path}",
                    headers={
                        "Accept": "application/vnd.github+json",
                        **supplied_headers,
                        "Authorization": f"Bearer {token}",
                    },
                    timeout=30,
                    **kwargs,
                )
            except httpx.TransportError:
                if method != "GET" or attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)
                continue
            if method == "GET" and attempt < 2:
                if response.status_code == 401:
                    self.app.invalidate(self.installation_id)
                    continue
                delay = None
                if response.status_code in (403, 429):
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        delay = int(retry_after)
                    elif response.headers.get("X-RateLimit-Remaining") == "0":
                        delay = max(
                            1,
                            int(response.headers["X-RateLimit-Reset"])
                            - int(time.time())
                            + 1,
                        )
                elif response.status_code >= 500:
                    delay = 2**attempt
                if delay is not None and delay <= 60:
                    await asyncio.sleep(delay)
                    continue
            response.raise_for_status()
            return response
        raise RuntimeError("GitHub request exhausted retries")


@cache
def get_github_app() -> GitHubApp:
    return GitHubApp()
