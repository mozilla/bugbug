"""tests.firefox.dev flakiness lookup for the test-repair gate.

The tests.firefox.dev dashboard (github.com/mozilla/aretestsfastyet) is static JS
that reads pre-aggregated timings JSON from the Firefox CI Taskcluster index at
runtime. We consume the same public artifacts (no auth) to get a test's cross-push
flakiness, so the listener can skip clearly intermittent tests before spending an
agent run.

Only ``mozilla-central`` / ``try`` datasets are published (there is no autoland
index), so this is a flakiness signal only; the autoland last-green push and the
candidate commit range come from mozci. The decode logic (bucket hashing and
per-test stats) is a direct port of the dashboard's ``common-test-data.js``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_INDEX_URL = (
    "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/"
    "gecko.v2.{repo}.latest.source.test-info-{harness}-timings/artifacts/public/{filename}"
)
_TIMEOUT = 30
_TOTAL_CHUNKS = 64


@dataclass(frozen=True)
class Flakiness:
    """Cross-push pass/fail stats for one test over the timings window."""

    total: int = 0
    passes: int = 0
    fails: int = 0
    timeouts: int = 0
    crashes: int = 0
    skips: int = 0
    # Most recent day offset (relative to metadata.startDate) the test was green,
    # or None if it never passed in the window. Coarse context only.
    last_green_day: int | None = None

    @property
    def failure_rate(self) -> float:
        """Fraction of non-skipped runs that failed/timed-out/crashed (0 if none)."""
        denom = self.passes + self.fails + self.timeouts + self.crashes
        if denom == 0:
            return 0.0
        return (self.fails + self.timeouts + self.crashes) / denom


def _to_int32(n: int) -> int:
    """Wrap to a signed 32-bit int, matching JavaScript's ``| 0``."""
    n &= 0xFFFFFFFF
    return n - 0x100000000 if n >= 0x80000000 else n


def _chunk_index(full_path: str, total_chunks: int = _TOTAL_CHUNKS) -> int:
    """Bucket a test path to 0..63; port of the dashboard's ``getChunkIndex``."""
    h = 0
    for ch in full_path:
        h = _to_int32((h << 5) - h + ord(ch))
    return ((h % total_chunks) + total_chunks) % total_chunks


def _fetch_bucket(harness: str, chunk: int, repo: str) -> dict:
    filename = f"{harness}-{chunk:02x}.json"
    url = _INDEX_URL.format(repo=repo, harness=harness, filename=filename)
    resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def _decompress_days(days: list[int]) -> list[int]:
    """Days are stored as offsets from the previous entry; return absolute days."""
    out: list[int] = []
    acc = 0
    for d in days:
        acc += d
        out.append(acc)
    return out


def _group_len(group: dict) -> int:
    for key in ("counts", "durations", "taskIdIds"):
        if key in group:
            return len(group[key])
    return len(group.get("days") or [])


def _count_at(group: dict, i: int) -> int:
    """Runs recorded at index ``i`` of a status group; port of ``getCountAtIndex``."""
    if "counts" in group:
        return group["counts"][i]
    if "durations" in group:
        return len(group["durations"][i])
    if "taskIdIds" in group:
        return len(group["taskIdIds"][i])
    return 1


def _classify(status: str) -> str | None:
    """Map a status string to pass/fail/timeout/crash/skip (None = ignored)."""
    if status == "SKIP":
        return "skip"
    if status == "CRASH":
        return "crash"
    if status.startswith("TIMEOUT"):
        return "timeout"
    if status == "UNKNOWN":
        return None
    # PASS-*, OK and EXPECTED-FAIL are all treated as green by the dashboard.
    if status.startswith("PASS") or status in ("OK", "EXPECTED-FAIL"):
        return "pass"
    return "fail"


def _find_test_id(data: dict, test_path: str) -> int | None:
    tables = data.get("tables") or {}
    info = data.get("testInfo") or {}
    runs = data.get("testRuns") or []
    paths = tables.get("testPaths") or []
    names = tables.get("testNames") or []
    path_ids = info.get("testPathIds") or []
    name_ids = info.get("testNameIds") or []
    for test_id, group in enumerate(runs):
        if not group or test_id >= len(path_ids) or test_id >= len(name_ids):
            continue
        dir_path = paths[path_ids[test_id]]
        test_name = names[name_ids[test_id]]
        full = f"{dir_path}/{test_name}" if dir_path else test_name
        if full == test_path:
            return test_id
    return None


def _compute_stats(data: dict, test_path: str) -> Flakiness:
    """Aggregate a test's status groups into a :class:`Flakiness`."""
    statuses = (data.get("tables") or {}).get("statuses") or []
    test_id = _find_test_id(data, test_path)
    if test_id is None:
        return Flakiness()
    test_group = (data.get("testRuns") or [])[test_id]

    counts = {"pass": 0, "fail": 0, "timeout": 0, "crash": 0, "skip": 0}
    pass_days: set[int] = set()
    bad_days: set[int] = set()
    for status_id, group in enumerate(test_group):
        if not group or status_id >= len(statuses):
            continue
        kind = _classify(statuses[status_id])
        if kind is None:
            continue
        n = _group_len(group)
        days = _decompress_days(group.get("days") or list(range(n)))
        for i in range(n):
            c = _count_at(group, i)
            counts[kind] += c
            if c > 0 and i < len(days):
                if kind == "pass":
                    pass_days.add(days[i])
                elif kind in ("fail", "timeout", "crash"):
                    bad_days.add(days[i])

    green = pass_days - bad_days
    return Flakiness(
        total=sum(counts.values()),
        passes=counts["pass"],
        fails=counts["fail"],
        timeouts=counts["timeout"],
        crashes=counts["crash"],
        skips=counts["skip"],
        last_green_day=max(green) if green else None,
    )


def get_flakiness(
    test_path: str, harness: str, repo: str = "mozilla-central"
) -> Flakiness:
    """Cross-push flakiness for a test from the tests.firefox.dev timings bucket.

    Fails soft: any network/parse error returns an empty :class:`Flakiness` so the
    listener gate treats the test as non-flaky (and errs toward triggering a run).
    """
    try:
        data = _fetch_bucket(harness, _chunk_index(test_path), repo)
    except httpx.HTTPStatusError as exc:
        # Only some harnesses publish a timings dataset; a 404 means there is
        # nothing to look up, so the gate passes the test through unjudged.
        if exc.response.status_code == 404:
            logger.info(
                "No tests.firefox.dev dataset for harness %s; "
                "skipping the intermittent check for %s",
                harness,
                test_path,
            )
        else:
            logger.warning(
                "tests.firefox.dev lookup failed for %s (%s): %s",
                test_path,
                harness,
                exc,
            )
        return Flakiness()
    except Exception:
        logger.exception(
            "tests.firefox.dev lookup failed for %s (%s)", test_path, harness
        )
        return Flakiness()
    return _compute_stats(data, test_path)
