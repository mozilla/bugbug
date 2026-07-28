import json
from pathlib import Path

import pytest
from app import flakiness

FIXTURE = Path(__file__).parent / "fixtures" / "xpcshell-bucket.json"


@pytest.fixture
def bucket() -> dict:
    return json.loads(FIXTURE.read_text())


@pytest.fixture(autouse=True)
def _clear_bucket_cache():
    flakiness._buckets.clear()
    yield
    flakiness._buckets.clear()


def _stats(bucket: dict, test_path: str) -> flakiness.Flakiness:
    return flakiness._decode_bucket(bucket).get(test_path, flakiness.Flakiness())


def test_chunk_index_matches_reference_hash():
    # Port of the dashboard's getChunkIndex (32-bit signed string hash mod 64).
    assert flakiness._chunk_index("") == 0
    assert flakiness._chunk_index("a") == 33
    assert flakiness._chunk_index("ab") == 33
    # Always in range and deterministic.
    idx = flakiness._chunk_index("dom/base/test/test_foo.js")
    assert 0 <= idx < 64
    assert idx == flakiness._chunk_index("dom/base/test/test_foo.js")


def test_decode_bucket_counts_and_rate(bucket):
    stats = _stats(bucket, "dom/base/test/test_foo.js")
    # foo: 3 passes (durations 2+1), 1 fail, 1 timeout, no skip/crash.
    assert stats.passes == 3
    assert stats.fails == 1
    assert stats.timeouts == 1
    assert stats.crashes == 0
    assert stats.skips == 0
    assert stats.total == 5
    # failure_rate counts fail+timeout+crash over pass+fail+timeout+crash = 2/5.
    assert stats.failure_rate == pytest.approx(0.4)


def test_decode_bucket_covers_every_test_in_one_pass(bucket):
    # Decoded once per bucket and cached, so every test in it must be present.
    assert set(flakiness._decode_bucket(bucket)) == {
        "dom/base/test/test_foo.js",
        "dom/base/test/test_bar.js",
    }


def test_decode_bucket_skip_only_test(bucket):
    stats = _stats(bucket, "dom/base/test/test_bar.js")
    assert stats.skips == 4
    assert stats.passes == 0
    assert stats.fails == 0
    # No pass/fail/timeout/crash -> rate is 0, not a division error.
    assert stats.failure_rate == 0.0


def test_decode_bucket_unknown_test_returns_empty(bucket):
    stats = _stats(bucket, "does/not/exist.js")
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


def test_get_flakiness_fails_soft_on_unexpected_bucket_shape(monkeypatch):
    # The bucket layout is an undocumented internal format of the dashboard, so a
    # shape change must not escape and abort the whole failing task.
    monkeypatch.setattr(
        flakiness, "_fetch_bucket", lambda *a, **k: ["not", "a", "dict"]
    )
    stats = flakiness.get_flakiness("dom/base/test/test_foo.js", "xpcshell")
    assert stats.total == 0


def _http_error(status_code):
    request = flakiness.httpx.Request("GET", "https://example/timings.json")
    response = flakiness.httpx.Response(status_code, request=request)
    return flakiness.httpx.HTTPStatusError("boom", request=request, response=response)


def test_unpublished_harness_404_is_not_an_error(monkeypatch, caplog):
    # Only some harnesses publish a timings dataset (e.g. reftest does not).
    # A 404 is an expected "no data", not an error worth a traceback/Sentry event.
    def not_found(*a, **k):
        raise _http_error(404)

    monkeypatch.setattr(flakiness, "_fetch_bucket", not_found)
    with caplog.at_level("DEBUG"):
        stats = flakiness.get_flakiness("editor/reftests/a.html", "reftest")

    assert stats.total == 0
    assert not [r for r in caplog.records if r.levelname in ("ERROR", "WARNING")]
    assert not any(r.exc_info for r in caplog.records)


def test_unexpected_http_status_warns_without_traceback(monkeypatch, caplog):
    def server_error(*a, **k):
        raise _http_error(500)

    monkeypatch.setattr(flakiness, "_fetch_bucket", server_error)
    with caplog.at_level("DEBUG"):
        stats = flakiness.get_flakiness("dom/base/test/test_foo.js", "xpcshell")

    assert stats.total == 0
    assert [r for r in caplog.records if r.levelname == "WARNING"]


def test_harness_detection():
    # xpcshell via the test-suite tag, and via the label when the suite is generic.
    assert flakiness._harness("xpcshell", "irrelevant") == "xpcshell"
    assert flakiness._harness("test", "test-linux/opt-xpcshell-4") == "xpcshell"
    assert flakiness._harness("mochitest-browser-chrome", "l") == "mochitest"
    # Recognized from the label too: the same suite arrives with no test-suite tag,
    # and the Taskcluster kind ("test") is not a harness.
    assert (
        flakiness._harness("", "test-linux2404-64/opt-mochitest-browser-chrome-8")
        == "mochitest"
    )
    # Harnesses that publish no timings dataset resolve to None (no lookup).
    assert flakiness._harness("web-platform-tests", "l") is None
    assert flakiness._harness("", "test-linux/opt-reftest-1") is None


def test_bucket_is_fetched_once_for_every_test_in_it(monkeypatch, bucket):
    # The dataset changes a few times a day and a real bucket is tens of MB, so a
    # second test in the same bucket must not refetch it.
    calls = []

    def fake_fetch(harness, chunk, repo):
        calls.append(chunk)
        return bucket

    monkeypatch.setattr(flakiness, "_fetch_bucket", fake_fetch)
    foo = "dom/base/test/test_foo.js"
    assert flakiness._chunk_index(foo) == flakiness._chunk_index(foo)
    flakiness.get_flakiness(foo, "xpcshell")
    flakiness.get_flakiness(foo, "xpcshell")
    assert calls == [flakiness._chunk_index(foo)]


def test_intermittent_tests_skips_lookup_without_a_dataset(monkeypatch):
    # reftest/wpt publish nothing, so the lookup would be a guaranteed 404.
    called = []
    monkeypatch.setattr(
        flakiness, "_fetch_bucket", lambda *a, **k: called.append(a) or {}
    )
    assert (
        flakiness.intermittent_tests(["a.html"], "reftest", "test-linux/reftest-1")
        == set()
    )
    assert called == []


def test_intermittent_tests_flags_only_tests_over_the_threshold(monkeypatch):
    rates = {"flaky.js": 0.30, "solid.js": 0.001}
    monkeypatch.setattr(
        flakiness,
        "get_flakiness",
        lambda test, harness, repo="mozilla-central": flakiness.Flakiness(
            total=1000,
            passes=int(1000 * (1 - rates[test])),
            fails=int(1000 * rates[test]),
        ),
    )
    flaky = flakiness.intermittent_tests(
        ["flaky.js", "solid.js"], "xpcshell", "test-linux/opt-xpcshell-1"
    )
    assert flaky == {"flaky.js"}


def test_few_percent_failure_rate_counts_as_intermittent(monkeypatch):
    # The timings dataset records one entry per task run per day, so even the
    # flakiest tests sit at a few percent. A threshold tuned for a per-push rate
    # would sit above every real intermittent and never fire.
    monkeypatch.setattr(
        flakiness,
        "get_flakiness",
        lambda *a, **k: flakiness.Flakiness(total=2000, passes=1880, fails=120),
    )
    flaky = flakiness.intermittent_tests(
        ["a.js"], "xpcshell", "test-linux/opt-xpcshell-1"
    )
    assert flaky == {"a.js"}
