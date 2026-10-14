"""Tests for the tests.firefox.dev history gate."""

import httpx
import pytest
from app import flakiness

CLEAN = "browser/base/content/test/browser_clean.js"
FLAKY = "browser/base/content/test/browser_flaky.js"


def _bucket(**tests):
    """A decoded bucket: ``path -> Flakiness``, given ``name=(passes, failures)``."""
    return {
        path: flakiness.Flakiness(passes=passes, fails=fails)
        for path, (passes, fails) in tests.items()
    }


@pytest.fixture(autouse=True)
def clear_cache():
    flakiness._buckets.clear()
    yield
    flakiness._buckets.clear()


@pytest.fixture
def stats(monkeypatch):
    """Stub the bucket lookup with one flat ``path -> Flakiness`` mapping."""

    def use(mapping):
        monkeypatch.setattr(
            flakiness, "_bucket_stats", lambda harness, chunk: dict(mapping)
        )

    return use


def test_a_test_that_never_failed_is_not_intermittent(stats):
    stats(_bucket(**{CLEAN: (5000, 0)}))
    assert flakiness.has_clean_history([CLEAN], "", "test-linux/opt-mochitest-1")


def test_a_failure_rate_under_the_threshold_is_still_clean(stats):
    # 1 in 10000 is not a flaky test; demanding a spotless record instead would
    # abstain on most of the regressions this gate exists to speed up.
    stats(_bucket(**{CLEAN: (9999, 1)}))
    assert flakiness.has_clean_history([CLEAN], "", "test-linux/opt-mochitest-1")


def test_a_failure_rate_over_the_threshold_keeps_the_wait(stats):
    stats(_bucket(**{FLAKY: (999, 1)}))
    assert not flakiness.has_clean_history([FLAKY], "", "test-linux/opt-mochitest-1")


def test_one_flaky_test_among_clean_ones_keeps_the_wait(stats):
    stats(_bucket(**{CLEAN: (5000, 0), FLAKY: (999, 1)}))
    assert not flakiness.has_clean_history(
        [CLEAN, FLAKY], "", "test-linux/opt-mochitest-1"
    )


def test_timeouts_and_crashes_count_as_failures(stats):
    stats({CLEAN: flakiness.Flakiness(passes=999, timeouts=1)})
    assert not flakiness.has_clean_history([CLEAN], "", "test-linux/opt-mochitest-1")

    stats({CLEAN: flakiness.Flakiness(passes=999, crashes=1)})
    assert not flakiness.has_clean_history([CLEAN], "", "test-linux/opt-mochitest-1")


def test_a_test_with_too_little_history_keeps_the_wait(stats):
    stats(_bucket(**{CLEAN: (99, 0)}))
    assert not flakiness.has_clean_history([CLEAN], "", "test-linux/opt-mochitest-1")


def test_skips_are_not_counted_as_evidence(stats):
    stats({CLEAN: flakiness.Flakiness(passes=10, skips=5000)})
    assert not flakiness.has_clean_history([CLEAN], "", "test-linux/opt-mochitest-1")


def test_a_test_the_dataset_does_not_cover_keeps_the_wait(stats):
    stats(_bucket(**{CLEAN: (5000, 0)}))
    assert not flakiness.has_clean_history(
        ["dom/base/test/test_brand_new.html"], "", "test-linux/opt-mochitest-1"
    )


def test_no_tests_keeps_the_wait(stats):
    stats(_bucket(**{CLEAN: (5000, 0)}))
    assert not flakiness.has_clean_history([], "", "test-linux/opt-mochitest-1")


@pytest.mark.parametrize(
    ("suite", "label"),
    [
        ("", "test-linux2404-64/opt-web-platform-tests-3"),
        ("reftest", "test-linux2404-64/opt-reftest-2"),
        ("", "test-linux2404-64/opt-gtest"),
    ],
)
def test_a_harness_without_a_dataset_keeps_the_wait(stats, suite, label):
    stats(_bucket(**{CLEAN: (5000, 0)}))
    assert not flakiness.has_clean_history([CLEAN], suite, label)


@pytest.mark.parametrize(
    ("suite", "label", "harness"),
    [
        ("", "test-linux2404-64/opt-mochitest-plain-1", "mochitest"),
        ("", "test-linux2404-64/opt-mochitest-browser-chrome-3", "mochitest"),
        ("browser-chrome", "test-linux2404-64/debug-test-3", "mochitest"),
        ("", "test-linux2404-64/opt-xpcshell-5", "xpcshell"),
        ("xpcshell", "test-linux2404-64/debug-test-1", "xpcshell"),
    ],
)
def test_the_harness_is_read_from_either_the_suite_or_the_label(suite, label, harness):
    assert flakiness._harness(suite, label) == harness


def test_a_lookup_error_keeps_the_wait(monkeypatch):
    def boom(harness, chunk):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(flakiness, "_bucket_stats", boom)
    assert not flakiness.has_clean_history([CLEAN], "", "test-linux/opt-mochitest-1")


def test_a_missing_dataset_keeps_the_wait(monkeypatch):
    request = httpx.Request("GET", "https://index.example/artifact")

    def missing(harness, chunk):
        raise httpx.HTTPStatusError(
            "404", request=request, response=httpx.Response(404, request=request)
        )

    monkeypatch.setattr(flakiness, "_bucket_stats", missing)
    assert not flakiness.has_clean_history([CLEAN], "", "test-linux/opt-mochitest-1")


def test_each_bucket_is_fetched_once(monkeypatch):
    calls = []

    def fake_fetch(harness, chunk):
        calls.append((harness, chunk))
        return _RAW_BUCKET

    monkeypatch.setattr(flakiness, "_fetch_bucket", fake_fetch)
    first = flakiness.get_flakiness("dom/base/test/test_a.html", "mochitest")
    second = flakiness.get_flakiness("dom/base/test/test_a.html", "mochitest")
    assert first == second
    assert len(calls) == 1


# One test recorded as 3 PASS and 1 FAIL, in the dashboard's table-indexed shape.
_RAW_BUCKET = {
    "tables": {
        "testPaths": ["dom/base/test"],
        "testNames": ["test_a.html"],
        "statuses": ["PASS", "FAIL", "SKIP"],
    },
    "testInfo": {"testPathIds": [0], "testNameIds": [0]},
    "testRuns": [[{"counts": [3]}, {"counts": [1]}, None]],
}


def test_the_dashboard_bucket_format_is_decoded():
    decoded = flakiness._decode_bucket(_RAW_BUCKET)
    assert decoded == {
        "dom/base/test/test_a.html": flakiness.Flakiness(passes=3, fails=1)
    }


def test_the_chunk_index_matches_the_dashboard_hash():
    # Port of getChunkIndex; these are the values the published buckets are keyed by,
    # so a drift here would silently look up the wrong bucket and find no history.
    assert flakiness._chunk_index("") == 0
    assert flakiness._chunk_index("a") == 33
    assert 0 <= flakiness._chunk_index("dom/base/test/test_a.html") < 64
