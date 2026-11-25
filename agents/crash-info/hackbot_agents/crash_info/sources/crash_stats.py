"""Crash-stats (Socorro) public API: aggregate data for a signature and the
public part of individual processed crashes."""

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

from .http import HttpClient, LeakageBlocked

# Public SuperSearch fields worth aggregating for missing-information work:
# configuration (product, version, OS, arch, channel), runtime state (process
# type, startup crash, shutdown progress) and object state (reason, address).
DEFAULT_FACETS = (
    "product",
    "version",
    "platform",
    "platform_pretty_version",
    "cpu_arch",
    "release_channel",
    "process_type",
    "startup_crash",
    "shutdown_progress",
    "reason",
    "address",
    "moz_crash_reason",
)

REPORT_FIELDS = (
    "signature",
    "product",
    "version",
    "release_channel",
    "os_pretty_version",
    "cpu_arch",
    "process_type",
    "reason",
    "address",
    "moz_crash_reason",
    "uptime",
    "startup_crash",
    "crashing_thread_name",
)


def crash_date(crash_id: str) -> date | None:
    """Socorro crash ids end with the submission date as YYMMDD."""
    tail = crash_id.strip()[-6:]
    try:
        return datetime.strptime(tail, "%y%m%d").date()
    except ValueError:
        return None


class CrashStatsClient:
    def __init__(self, http: HttpClient, base_url: str) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")

    def signature_url(self, signature: str) -> str:
        return f"{self.base_url}/signature/?signature={quote(signature)}"

    def report_url(self, crash_id: str) -> str:
        return f"{self.base_url}/report/index/{crash_id}"

    @staticmethod
    def _search(
        signature: str, as_of: datetime | None, window_days: int
    ) -> list[tuple[str, str]]:
        """SuperSearch criteria: the signature exactly, or the signatures that
        contain it when it starts with '~'; with a cutoff, only the crashes
        submitted in the days before it, as a developer could have looked them
        up at the time."""
        exact = signature if signature.startswith("~") else "=" + signature
        params = [("signature", exact)]
        if as_of is not None:
            start = as_of - timedelta(days=window_days)
            params += [
                ("date", ">=" + start.strftime("%Y-%m-%dT%H:%M:%S")),
                ("date", "<" + as_of.strftime("%Y-%m-%dT%H:%M:%S")),
            ]
        return params

    async def signature_facets(
        self,
        signature: str,
        *,
        as_of: datetime | None = None,
        window_days: int = 14,
        facets: tuple[str, ...] = DEFAULT_FACETS,
    ) -> dict[str, Any]:
        params = [
            *self._search(signature, as_of, window_days),
            ("_results_number", "0"),
            ("_facets_size", "6"),
            *[("_facets", name) for name in facets],
        ]
        data = await self.http.get_json(f"{self.base_url}/api/SuperSearch/", params)
        return {
            "total": data.get("total", 0),
            "facets": {
                name: [(str(t["term"]), int(t["count"])) for t in terms]
                for name, terms in (data.get("facets") or {}).items()
                if terms
            },
        }

    async def signature_crashes(
        self,
        signature: str,
        *,
        as_of: datetime | None = None,
        window_days: int = 14,
        limit: int = 3,
    ) -> list[dict[str, str]]:
        """The latest crashes with a signature (before the cutoff): their ids and
        submission dates, newest first."""
        params = [
            *self._search(signature, as_of, window_days),
            ("_columns", "uuid"),
            ("_columns", "date"),
            ("_sort", "-date"),
            ("_results_number", str(limit)),
        ]
        data = await self.http.get_json(f"{self.base_url}/api/SuperSearch/", params)
        return [
            {"uuid": str(hit["uuid"]), "date": str(hit.get("date") or "")[:10]}
            for hit in data.get("hits") or []
            if hit.get("uuid")
        ]

    async def crash_report(
        self, crash_id: str, *, as_of: datetime | None = None
    ) -> dict[str, Any]:
        submitted = crash_date(crash_id)
        if as_of is not None and submitted is not None and submitted > as_of.date():
            raise LeakageBlocked(f"crash {crash_id} was submitted after the cutoff")
        data = await self.http.get_json(
            f"{self.base_url}/api/ProcessedCrash/",
            {"crash_id": crash_id, "datatype": "processed"},
        )
        report = {
            key: data.get(key)
            for key in REPORT_FIELDS
            if data.get(key) not in (None, "")
        }
        report["frames"] = _crashing_frames(data)
        return report


def _crashing_frames(data: dict[str, Any], limit: int = 15) -> list[str]:
    dump = data.get("json_dump") or {}
    threads = dump.get("threads") or []
    index = data.get("crashing_thread")
    if index is None:
        index = (dump.get("crash_info") or {}).get("crashing_thread")
    if not isinstance(index, int) or not 0 <= index < len(threads):
        return []
    frames = []
    for frame in threads[index].get("frames", [])[:limit]:
        function = frame.get("function") or frame.get("module_offset") or "?"
        where = f" ({frame['file']}:{frame.get('line')})" if frame.get("file") else ""
        frames.append(
            f"{frame.get('frame', len(frames))} {frame.get('module', '')} {function}{where}".strip()
        )
    return frames
