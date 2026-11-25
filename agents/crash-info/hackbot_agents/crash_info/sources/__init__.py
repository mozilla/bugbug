"""Evidence sources the agent may consult (all read-only)."""

import os
from dataclasses import dataclass
from datetime import datetime

import httpx

from ..settings import SourceSettings
from .bugzilla import BugView, BugzillaClient
from .code import CodeSource, LocalCode, RemoteCode
from .crash_stats import CrashStatsClient
from .hgmo import HgmoClient
from .http import HttpClient, LeakageBlocked, NotFound, SourceError
from .local_repo import LocalRepo
from .searchfox import CodeHit, SearchfoxClient

__all__ = [
    "BugView",
    "CodeHit",
    "CodeSource",
    "LeakageBlocked",
    "NotFound",
    "SourceError",
    "Sources",
    "build_sources",
]


@dataclass
class Sources:
    http: HttpClient
    bugzilla: BugzillaClient
    crash_stats: CrashStatsClient
    searchfox: SearchfoxClient
    hgmo: HgmoClient
    local: LocalRepo | None = None
    code: CodeSource | None = None
    crash_stats_window_days: int = 14

    async def pin_code(
        self, as_of: datetime | None, revision: str | None = None
    ) -> str:
        """Choose the code source and the revision it reads (``revision`` when
        resuming a state that already pinned one); returns that revision."""
        if self.local is not None:
            if revision is None:
                revision = await (
                    self.local.revision_at(as_of)
                    if as_of
                    else self.local.resolve("HEAD")
                )
            if revision is None:
                raise SourceError(f"the local checkout has no commit before {as_of}")
            self.code = LocalCode(self.local, revision=revision, as_of=as_of)
            return revision
        if revision is None and as_of is not None:
            revision = await self.hgmo.revision_at(as_of)
            if revision is None:
                raise SourceError(f"no mozilla-central push found before {as_of}")
        self.code = RemoteCode(
            self.searchfox, self.hgmo, revision=revision, as_of=as_of
        )
        return self.code.revision

    async def aclose(self) -> None:
        await self.http.aclose()


def build_sources(
    settings: SourceSettings, *, transport: httpx.AsyncBaseTransport | None = None
) -> Sources:
    http = HttpClient(
        user_agent=settings.user_agent,
        timeout_s=settings.timeout_s,
        cache_dir=settings.cache_dir,
        cache_ttl_s=settings.cache_ttl_s,
        transport=transport,
    )
    return Sources(
        http=http,
        bugzilla=BugzillaClient(
            http, settings.bugzilla_url, os.environ.get(settings.bugzilla_api_key_env)
        ),
        crash_stats=CrashStatsClient(http, settings.crash_stats_url),
        searchfox=SearchfoxClient(
            http, settings.searchfox_url, settings.searchfox_repo
        ),
        hgmo=HgmoClient(http, settings.hgmo_url, settings.hgmo_repo),
        local=LocalRepo(settings.local_repo.expanduser())
        if settings.local_repo
        else None,
        crash_stats_window_days=settings.crash_stats_window_days,
    )
