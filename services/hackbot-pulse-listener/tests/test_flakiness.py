import json
from pathlib import Path

import pytest
from app import flakiness

FIXTURE = Path(__file__).parent / "fixtures" / "xpcshell-bucket.json"


@pytest.fixture
def bucket() -> dict:
    return json.loads(FIXTURE.read_text())


def test_chunk_index_matches_reference_hash():
    # Port of the dashboard's getChunkIndex (32-bit signed string hash mod 64).
    assert flakiness._chunk_index("") == 0
    assert flakiness._chunk_index("a") == 33
    assert flakiness._chunk_index("ab") == 33
    # Always in range and deterministic.
    idx = flakiness._chunk_index("dom/base/test/test_foo.js")
    assert 0 <= idx < 64
    assert idx == flakiness._chunk_index("dom/base/test/test_foo.js")


def test_compute_stats_counts_and_rate(bucket):
    stats = flakiness._compute_stats(bucket, "dom/base/test/test_foo.js")
    # foo: 3 passes (durations 2+1), 1 fail, 1 timeout, no skip/crash.
    assert stats.passes == 3
    assert stats.fails == 1
    assert stats.timeouts == 1
    assert stats.crashes == 0
    assert stats.skips == 0
    assert stats.total == 5
    # failure_rate counts fail+timeout+crash over pass+fail+timeout+crash = 2/5.
    assert stats.failure_rate == pytest.approx(0.4)


def test_compute_stats_last_green_day(bucket):
    # Passes on days 0 and 2; a fail on day 2 and a timeout on day 1, so only day 0
    # is green. Days are differentially compressed and decoded by running sum.
    stats = flakiness._compute_stats(bucket, "dom/base/test/test_foo.js")
    assert stats.last_green_day == 0


def test_compute_stats_skip_only_test(bucket):
    stats = flakiness._compute_stats(bucket, "dom/base/test/test_bar.js")
    assert stats.skips == 4
    assert stats.passes == 0
    assert stats.fails == 0
    # No pass/fail/timeout/crash -> rate is 0, not a division error.
    assert stats.failure_rate == 0.0


def test_compute_stats_unknown_test_returns_empty(bucket):
    stats = flakiness._compute_stats(bucket, "does/not/exist.js")
    assert stats.total == 0
    assert stats.failure_rate == 0.0


def test_get_flakiness_uses_bucket(monkeypatch, bucket):
    captured = {}

    def fake_fetch(harness, chunk, repo):
        captured["harness"] = harness
        captured["chunk"] = chunk
        captured["repo"] = repo
        return bucket

    monkeypatch.setattr(flakiness, "_fetch_bucket", fake_fetch)
    stats = flakiness.get_flakiness("dom/base/test/test_foo.js", "xpcshell")
    assert stats.passes == 3
    assert captured["harness"] == "xpcshell"
    assert captured["repo"] == "mozilla-central"
    assert captured["chunk"] == flakiness._chunk_index("dom/base/test/test_foo.js")


def test_get_flakiness_fails_soft_on_network_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(flakiness, "_fetch_bucket", boom)
    stats = flakiness.get_flakiness("dom/base/test/test_foo.js", "xpcshell")
    assert stats.total == 0
    assert stats.failure_rate == 0.0
