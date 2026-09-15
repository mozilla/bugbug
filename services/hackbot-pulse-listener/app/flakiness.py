"""Historical pass/fail record of a test, from the tests.firefox.dev timings data.

Used to decide that a failing test is not an intermittent, so the listener need not
wait for a sheriff to say so. A test with three weeks of CI runs behind it and next
to no failures among them is not flaky; when it fails, it regressed.

The tests.firefox.dev dashboard (github.com/mozilla/aretestsfastyet) is static JS
that reads pre-aggregated timings JSON from the Firefox CI Taskcluster index at
runtime. We consume the same public artifacts (no auth). The decode logic (bucket
hashing and per-test stats) is a direct port of the dashboard's
``common-test-data.js``.

Only ``mozilla-central`` / ``try`` datasets are published, and only for the mochitest
and xpcshell harnesses, so this answers for a subset of failures and abstains on the
rest.
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
_REPO = "mozilla-central"
_TIMEOUT = 30
_TOTAL_CHUNKS = 64

# Decoded buckets, keyed by (harness, chunk). A raw bucket is ~17MB of JSON and the
# index republishes at most daily, so caching the (small) decoded stats avoids
# refetching it for every test; the cache is sized to hold every chunk of both
# harnesses. The lock covers the fetch and decode together: the worker pool is far
# larger than the number of buckets, and concurrent decodes would dominate the
# memory limit.
_BUCKET_TTL_SECONDS = 24 * 60 * 60
_buckets: TTLCache = TTLCache(maxsize=2 * _TOTAL_CHUNKS, ttl=_BUCKET_TTL_SECONDS)
_buckets_lock = threading.Lock()


@dataclass(frozen=True)
class Flakiness:
    """Cross-push pass/fail stats for one test over the timings window."""

    passes: int = 0
    fails: int = 0
    timeouts: int = 0
    crashes: int = 0
    skips: int = 0

    @property
    def runs(self) -> int:
        """Runs that produced a verdict; skips are not evidence either way."""
        return self.passes + self.fails + self.timeouts + self.crashes

    @property
    def failures(self) -> int:
        return self.fails + self.timeouts + self.crashes

    @property
    def failure_rate(self) -> float:
        return self.failures / self.runs if self.runs else 0.0


def _harness(suite: str, label: str) -> str | None:
    """The timings harness for a test task, or None if it publishes none.

    Only the mochitest and xpcshell datasets exist, so every other harness has no
    history to check against. The Taskcluster ``kind`` is not a harness name (it is
    often just "test"), so both the suite tag and the label are matched -- the same
    suite arrives tagged either way.
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


def _fetch_bucket(harness: str, chunk: int) -> dict:
    filename = f"{harness}-{chunk:02x}.json"
    url = _INDEX_URL.format(repo=_REPO, harness=harness, filename=filename)
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
            passes=counts["pass"],
            fails=counts["fail"],
            timeouts=counts["timeout"],
            crashes=counts["crash"],
            skips=counts["skip"],
        )
    return out


def _bucket_stats(harness: str, chunk: int) -> dict[str, Flakiness]:
    key = (harness, chunk)
    with _buckets_lock:
        stats = _buckets.get(key)
        if stats is None:
            stats = _decode_bucket(_fetch_bucket(harness, chunk))
            _buckets[key] = stats
        return stats


def get_flakiness(test_path: str, harness: str) -> Flakiness:
    """Historical record of a test, or an empty one if the data cannot say.

    Fails soft: a network or parse error, a harness with no dataset and a test the
    dataset does not cover all return zero runs, which every caller reads as "no
    evidence" rather than as "never failed".
    """
    try:
        return _bucket_stats(harness, _chunk_index(test_path)).get(
            test_path, Flakiness()
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.info(
                "No tests.firefox.dev dataset for harness %s (%s)", harness, test_path
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


def has_clean_history(tests: Iterable[str], suite: str, label: str) -> bool:
    """Whether none of these tests looks like an intermittent in the timings data.

    True only when the data covers every one of them with at least
    ``flakiness_min_runs`` runs and a failure rate no higher than
    ``flakiness_max_failure_rate``. Every unknown -- a harness that publishes no
    dataset, a test the dataset does not list, one too new to have a record --
    returns False, so the caller keeps whatever wait it would otherwise have done.
    """
    harness = _harness(suite, label)
    if harness is None:
        logger.info("No tests.firefox.dev dataset for %s", label)
        return False

    tests = set(tests)
    if not tests:
        return False

    for test in tests:
        stats = get_flakiness(test, harness)
        if stats.runs < settings.flakiness_min_runs:
            logger.info(
                "Only %s recorded runs of %s (need %s); cannot rule out an "
                "intermittent",
                stats.runs,
                test,
                settings.flakiness_min_runs,
            )
            return False
        if stats.failure_rate > settings.flakiness_max_failure_rate:
            logger.info(
                "Test %s failed %s of its last %s runs (%.3f%%); it may be an "
                "intermittent",
                test,
                stats.failures,
                stats.runs,
                100 * stats.failure_rate,
            )
            return False
    return True
