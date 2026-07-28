"""tests.firefox.dev flakiness gate for test-repair.

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
import threading
from collections.abc import Iterable
from dataclasses import dataclass

import httpx
from cachetools import TTLCache

from app.config import settings

logger = logging.getLogger(__name__)

_INDEX_URL = (
    "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/"
    "gecko.v2.{repo}.latest.source.test-info-{harness}-timings/artifacts/public/{filename}"
)
_TIMEOUT = 30
_TOTAL_CHUNKS = 64

# Decoded buckets, keyed by (harness, chunk, repo). The index publishes a new
# dataset at most a few times a day, and a raw bucket is tens of MB of JSON, so
# caching the (small) decoded stats avoids refetching it for every test. The lock
# covers the fetch and decode together: the worker pool is far larger than the
# number of buckets, and concurrent decodes would dominate the memory limit.
_BUCKET_TTL_SECONDS = 60 * 60
_buckets: TTLCache = TTLCache(maxsize=32, ttl=_BUCKET_TTL_SECONDS)
_buckets_lock = threading.Lock()


@dataclass(frozen=True)
class Flakiness:
    """Cross-push pass/fail stats for one test over the timings window."""

    total: int = 0
    passes: int = 0
    fails: int = 0
    timeouts: int = 0
    crashes: int = 0
    skips: int = 0

    @property
    def failure_rate(self) -> float:
        """Fraction of non-skipped runs that failed/timed-out/crashed (0 if none)."""
        denom = self.passes + self.fails + self.timeouts + self.crashes
        if denom == 0:
            return 0.0
        return (self.fails + self.timeouts + self.crashes) / denom


def _harness(suite: str, label: str) -> str | None:
    """The timings harness for a test task, or None if it publishes none.

    Only the mochitest and xpcshell datasets exist, so every other harness has no
    intermittent data to check against. The Taskcluster ``kind`` is not a harness
    name (it is often just "test"), so both the suite tag and the label are matched
    -- the same suite arrives tagged either way.
    """
    text = f"{suite or ''} {label or ''}"
    if "xpcshell" in text:
        return "xpcshell"
    if "mochitest" in text or "browser-chrome" in text:
        return "mochitest"
    return None


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


def _run_count(group: dict) -> int:
    """Runs recorded in a status group; port of ``getCountAtIndex`` over all indices."""
    if "counts" in group:
        return sum(group["counts"])
    for key in ("durations", "taskIdIds"):
        if key in group:
            return sum(len(entry) for entry in group[key])
    return len(group.get("days") or [])


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


def _test_paths(data: dict) -> dict[int, str]:
    """Full test path per test id, from the bucket's index tables."""
    tables = data.get("tables") or {}
    info = data.get("testInfo") or {}
    paths = tables.get("testPaths") or []
    names = tables.get("testNames") or []
    path_ids = info.get("testPathIds") or []
    name_ids = info.get("testNameIds") or []
    out = {}
    for test_id in range(min(len(path_ids), len(name_ids))):
        dir_path = paths[path_ids[test_id]]
        name = names[name_ids[test_id]]
        out[test_id] = f"{dir_path}/{name}" if dir_path else name
    return out


def _decode_bucket(data: dict) -> dict[str, Flakiness]:
    """Per-test stats for every test in a bucket."""
    statuses = (data.get("tables") or {}).get("statuses") or []
    paths = _test_paths(data)
    out: dict[str, Flakiness] = {}
    for test_id, test_group in enumerate(data.get("testRuns") or []):
        path = paths.get(test_id)
        if not test_group or path is None:
            continue
        counts = dict.fromkeys(("pass", "fail", "timeout", "crash", "skip"), 0)
        for status_id, group in enumerate(test_group):
            if not group or status_id >= len(statuses):
                continue
            kind = _classify(statuses[status_id])
            if kind is not None:
                counts[kind] += _run_count(group)
        out[path] = Flakiness(
            total=sum(counts.values()),
            passes=counts["pass"],
            fails=counts["fail"],
            timeouts=counts["timeout"],
            crashes=counts["crash"],
            skips=counts["skip"],
        )
    return out


def _bucket_stats(harness: str, chunk: int, repo: str) -> dict[str, Flakiness]:
    key = (harness, chunk, repo)
    with _buckets_lock:
        stats = _buckets.get(key)
        if stats is None:
            stats = _decode_bucket(_fetch_bucket(harness, chunk, repo))
            _buckets[key] = stats
        return stats


def get_flakiness(
    test_path: str, harness: str, repo: str = "mozilla-central"
) -> Flakiness:
    """Cross-push flakiness for a test from the tests.firefox.dev timings bucket.

    Fails soft: any network/parse error returns an empty :class:`Flakiness` so the
    listener gate treats the test as non-flaky (and errs toward triggering a run).
    """
    try:
        return _bucket_stats(harness, _chunk_index(test_path), repo).get(
            test_path, Flakiness()
        )
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
    except Exception:
        logger.exception(
            "tests.firefox.dev lookup failed for %s (%s)", test_path, harness
        )
    return Flakiness()


def intermittent_tests(tests: Iterable[str], suite: str, label: str) -> set[str]:
    """The tests whose historical failure rate marks them as clearly intermittent.

    Empty when the task's harness publishes no timings dataset, logged once for the
    task rather than once per test.
    """
    harness = _harness(suite, label)
    if harness is None:
        logger.info(
            "No tests.firefox.dev dataset for %s; skipping the intermittent check",
            label,
        )
        return set()

    flaky = set()
    for test in tests:
        rate = get_flakiness(test, harness).failure_rate
        if rate >= settings.flakiness_threshold:
            logger.info(
                "Test %s is intermittent (failure rate %.2f); skipping", test, rate
            )
            flaky.add(test)
    return flaky
