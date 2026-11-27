"""Read-only Bugzilla REST access.

With an ``as_of`` cutoff the bug is shown as a reader saw it then: later
comments and attachments are hidden, and fields are rolled back through the
bug's history. This is what keeps evaluations on historical crashes from
leaking the fix into the agent's input.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .http import HttpClient

BUG_FIELDS = [
    "id",
    "summary",
    "status",
    "resolution",
    "product",
    "component",
    "creation_time",
    "cf_crash_signature",
    "keywords",
    "severity",
    "priority",
    "platform",
    "op_sys",
    "version",
    "type",
    "regressed_by",
    "depends_on",
    "blocks",
    "see_also",
    "whiteboard",
    "dupe_of",
]
LIST_FIELDS = {"keywords", "regressed_by", "depends_on", "blocks", "see_also"}
INT_LIST_FIELDS = {"regressed_by", "depends_on", "blocks"}
# History records some fields under their database names.
HISTORY_ALIASES = {
    "bug_status": "status",
    "short_desc": "summary",
    "status_whiteboard": "whiteboard",
    "bug_severity": "severity",
    "rep_platform": "platform",
    "dependson": "depends_on",
}
SEARCH_FIELDS = (
    "id,summary,status,resolution,creation_time,product,component,cf_crash_signature"
)
PATCH_CONTENT_TYPES = {"text/x-phabricator-request", "text/x-github-pull-request"}


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Comment:
    id: int
    count: int
    author: str
    created: datetime
    text: str
    is_private: bool = False


@dataclass
class Attachment:
    id: int
    created: datetime
    description: str
    content_type: str
    file_name: str
    is_patch: bool
    is_obsolete: bool

    @property
    def looks_like_patch(self) -> bool:
        return self.is_patch or self.content_type in PATCH_CONTENT_TYPES


@dataclass
class BugView:
    """A bug as visible at a cutoff (or now, without one)."""

    fields: dict[str, Any]
    comments: list[Comment]
    attachments: list[Attachment]
    as_of: datetime | None = None
    hidden: dict[str, int] = field(default_factory=dict)
    rolled_back: list[str] = field(default_factory=list)

    @property
    def signatures(self) -> list[str]:
        return parse_signatures(self.fields.get("cf_crash_signature") or "")


def parse_signatures(raw: str) -> list[str]:
    """``"[@ foo]\\n[@ bar | baz]"`` -> ``["foo", "bar | baz"]``."""
    out: list[str] = []
    start = raw.find("[@")
    while start != -1:
        end = raw.find("]", start)
        if end == -1:
            break
        sig = raw[start + 2 : end].strip()
        if sig and sig not in out:
            out.append(sig)
        start = raw.find("[@", end)
    return out


class BugzillaClient:
    def __init__(
        self, http: HttpClient, base_url: str, api_key: str | None = None
    ) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @property
    def _headers(self) -> dict[str, str] | None:
        return {"X-BUGZILLA-API-KEY": self.api_key} if self.api_key else None

    def bug_url(self, bug_id: int, comment: int | None = None) -> str:
        url = f"{self.base_url}/show_bug.cgi?id={bug_id}"
        return f"{url}#c{comment}" if comment is not None else url

    async def _get(self, path: str, params: dict | None = None, *, cache: bool) -> Any:
        return await self.http.get_json(
            f"{self.base_url}/rest/{path}", params, headers=self._headers, cache=cache
        )

    async def bug_fields(self, bug_id: int, *, cache: bool = True) -> dict[str, Any]:
        data = await self._get(
            f"bug/{bug_id}", {"include_fields": ",".join(BUG_FIELDS)}, cache=cache
        )
        bugs = data.get("bugs") or []
        if not bugs:
            raise ValueError(f"bug {bug_id} is not visible (restricted or missing)")
        return bugs[0]

    async def comments(self, bug_id: int, *, cache: bool = True) -> list[Comment]:
        data = await self._get(f"bug/{bug_id}/comment", cache=cache)
        raw = data["bugs"][str(bug_id)]["comments"]
        return [
            Comment(
                id=c["id"],
                count=c["count"],
                author=c.get("creator") or c.get("author") or "",
                created=parse_time(c["creation_time"]),
                text=c.get("text") or "",
                is_private=bool(c.get("is_private")),
            )
            for c in raw
        ]

    async def attachments(self, bug_id: int, *, cache: bool = True) -> list[Attachment]:
        data = await self._get(
            f"bug/{bug_id}/attachment", {"exclude_fields": "data"}, cache=cache
        )
        raw = data["bugs"].get(str(bug_id)) or []
        return [
            Attachment(
                id=a["id"],
                created=parse_time(a["creation_time"]),
                description=a.get("description") or a.get("summary") or "",
                content_type=a.get("content_type") or "",
                file_name=a.get("file_name") or "",
                is_patch=bool(a.get("is_patch")),
                is_obsolete=bool(a.get("is_obsolete")),
            )
            for a in raw
        ]

    async def history(self, bug_id: int, *, cache: bool = True) -> list[dict[str, Any]]:
        data = await self._get(f"bug/{bug_id}/history", cache=cache)
        return data["bugs"][0]["history"]

    async def view(
        self, bug_id: int, as_of: datetime | None = None, *, cache: bool | None = None
    ) -> BugView:
        # Live reads skip the cache so a fresh comment is never missed.
        cache = as_of is not None if cache is None else cache
        fields, comments, attachments = await asyncio.gather(
            self.bug_fields(bug_id, cache=cache),
            self.comments(bug_id, cache=cache),
            self.attachments(bug_id, cache=cache),
        )
        view = BugView(
            fields=fields, comments=comments, attachments=attachments, as_of=as_of
        )
        if as_of is not None:
            history = await self.history(bug_id, cache=cache)
            view.fields, view.rolled_back = rollback_fields(fields, history, as_of)
            view.comments = [c for c in comments if c.created <= as_of]
            view.attachments = [a for a in attachments if a.created <= as_of]
            view.hidden = {
                "comments": len(comments) - len(view.comments),
                "attachments": len(attachments) - len(view.attachments),
            }
        view.comments = [c for c in view.comments if not c.is_private]
        return view

    async def search(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """A raw REST search; the caller filters the results by time."""
        data = await self._get("bug", params, cache=True)
        return data.get("bugs") or []

    async def search_signature(
        self,
        fragment: str,
        *,
        limit: int = 8,
        exclude: int | None = None,
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        data = await self._get(
            "bug",
            {
                "f1": "cf_crash_signature",
                "o1": "substring",
                "v1": fragment,
                "include_fields": SEARCH_FIELDS,
                "limit": 50,
            },
            cache=True,
        )
        return _visible(data.get("bugs") or [], exclude, as_of)[:limit]

    async def quicksearch(
        self,
        text: str,
        *,
        limit: int = 8,
        exclude: int | None = None,
        as_of: datetime | None = None,
    ) -> list[dict[str, Any]]:
        # Quicksearch reads "a:b" as a field search; quote such terms.
        terms = [
            f'"{t}"' if ":" in t and not t.startswith('"') else t for t in text.split()
        ]
        data = await self._get(
            "bug",
            {
                "quicksearch": " ".join(terms),
                "include_fields": SEARCH_FIELDS,
                "limit": 50,
            },
            cache=True,
        )
        return _visible(data.get("bugs") or [], exclude, as_of)[:limit]


def _visible(
    bugs: list[dict[str, Any]], exclude: int | None, as_of: datetime | None
) -> list[dict[str, Any]]:
    out = []
    for bug in bugs:
        if bug.get("id") == exclude:
            continue
        if as_of is not None:
            if parse_time(bug["creation_time"]) > as_of:
                continue
            # A related bug's current status may itself reveal the outcome.
            bug = {k: v for k, v in bug.items() if k not in ("status", "resolution")}
        out.append(bug)
    # Most recent first: recent reports of a signature matter most.
    return sorted(out, key=lambda b: b.get("creation_time", ""), reverse=True)


def rollback_fields(
    fields: dict[str, Any], history: list[dict[str, Any]], as_of: datetime
) -> tuple[dict[str, Any], list[str]]:
    """Undo, newest first, every recorded change made after ``as_of``."""
    current = dict(fields)
    rolled: set[str] = set()
    later = [h for h in history if parse_time(h["when"]) > as_of]
    for entry in sorted(later, key=lambda h: h["when"], reverse=True):
        for change in entry.get("changes", []):
            name = HISTORY_ALIASES.get(change["field_name"], change["field_name"])
            if name not in current:
                continue
            if name in LIST_FIELDS:
                current[name] = _undo_list_change(name, current.get(name) or [], change)
            else:
                current[name] = change.get("removed", "")
            rolled.add(name)
    if current.get("resolution") != "DUPLICATE":
        current.pop("dupe_of", None)
    return current, sorted(rolled)


def _undo_list_change(
    name: str, values: list[Any], change: dict[str, Any]
) -> list[Any]:
    added = _split(change.get("added", ""))
    removed = _split(change.get("removed", ""))
    kept = [v for v in values if str(v) not in added]
    for value in removed:
        coerced: Any = (
            int(value) if name in INT_LIST_FIELDS and value.isdigit() else value
        )
        if coerced not in kept:
            kept.append(coerced)
    return kept


def _split(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def first_patch_time(view: BugView) -> datetime | None:
    """When the first patch appeared: a patch-like attachment, or a landing comment."""
    times = [a.created for a in view.attachments if a.looks_like_patch]
    times += [c.created for c in view.comments if _is_landing_comment(c.text)]
    return min(times) if times else None


def _is_landing_comment(text: str) -> bool:
    return (
        text.startswith("Pushed by")
        or ("hg.mozilla.org/" in text and "/rev/" in text)
        or "github.com/mozilla-firefox/firefox/commit/" in text
    )


def cutoff_before_first_patch(view: BugView) -> datetime | None:
    first = first_patch_time(view)
    return first - timedelta(seconds=1) if first else None
