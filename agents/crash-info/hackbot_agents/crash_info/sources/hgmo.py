"""hg.mozilla.org: revision at a date, files and history at a revision, changesets.

Reading at a pinned revision is what lets historical evaluations look at the
code as it was before the fix (Searchfox only indexes the current tip).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from .http import HttpClient


def _iso(hg_date: Any) -> str:
    """hg dates are ``[unix_ts, tz_offset]``."""
    if isinstance(hg_date, list | tuple) and hg_date:
        return datetime.fromtimestamp(float(hg_date[0]), UTC).isoformat()
    return str(hg_date)


def _on_default(changeset: str | dict) -> bool:
    """Whether a push's tip is code. Release automation pushes its tags to
    mozilla-central on a branch of their own (tags-unified): commits that hold only
    .hgtags, with no source tree and no counterpart in the git repository."""
    return isinstance(changeset, str) or changeset.get("branch", "default") == "default"


class HgmoClient:
    def __init__(self, http: HttpClient, base_url: str, repo: str) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.repo = repo

    @property
    def _root(self) -> str:
        return f"{self.base_url}/{self.repo}"

    def rev_url(self, node: str) -> str:
        return f"{self._root}/rev/{node}"

    def file_url(self, rev: str, path: str, line: int | None = None) -> str:
        url = f"{self._root}/file/{rev}/{path}"
        return f"{url}#l{line}" if line else url

    async def revision_at(self, when: datetime) -> str | None:
        """The tip of the last push to the repo at or before ``when``."""
        cutoff = when.timestamp()
        for days in (3, 21, 120):
            # The server parses these dates in its own timezone; the window is
            # widened by a day and pushes are then filtered on exact timestamps.
            data = await self.http.get_json(
                f"{self._root}/json-pushes",
                {
                    "startdate": (when - timedelta(days=days)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "enddate": (when + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                    "version": "2",
                    "tipsonly": "1",
                    "full": "1",
                },
            )
            pushes = [
                p
                for p in (data.get("pushes") or {}).values()
                if p["date"] <= cutoff and _on_default(p["changesets"][-1])
            ]
            if pushes:
                last = max(pushes, key=lambda p: p["date"])
                changeset = last["changesets"][-1]
                return changeset if isinstance(changeset, str) else changeset["node"]
        return None

    async def file_at(self, rev: str, path: str) -> str:
        return await self.http.get_text(f"{self._root}/raw-file/{rev}/{path}")

    async def file_log(
        self, rev: str, path: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        data = await self.http.get_json(f"{self._root}/json-log/{rev}/{path}")
        return [
            {
                "node": e.get("node"),
                "date": _iso(e.get("date")),
                "user": e.get("user"),
                "desc": (e.get("desc") or "").splitlines()[0] if e.get("desc") else "",
            }
            for e in (data.get("entries") or [])[:limit]
        ]

    async def commit(self, node: str) -> dict[str, Any]:
        data = await self.http.get_json(f"{self._root}/json-rev/{node}")
        pushdate = data.get("pushdate")
        return {
            "node": data.get("node", node),
            "user": data.get("user"),
            "date": _iso(data.get("date")),
            "pushdate": _iso(pushdate) if pushdate else None,
            "push_ts": float(pushdate[0]) if pushdate else None,
            "desc": data.get("desc", ""),
            "files": [
                f.get("file") if isinstance(f, dict) else f
                for f in data.get("files", [])
            ],
        }

    async def commit_diff(self, node: str) -> str:
        return await self.http.get_text(f"{self._root}/raw-rev/{node}")
