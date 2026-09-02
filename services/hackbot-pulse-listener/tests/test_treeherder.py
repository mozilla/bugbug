"""Tests for the Treeherder classification gate."""

import httpx
import pytest
from app import treeherder


def _response(payload):
    request = httpx.Request("GET", "https://treeherder.example/api")
    return httpx.Response(200, json=payload, request=request)


def _job(classification):
    return {"failure_classification_id": classification, "task_id": "TT"}


@pytest.fixture
def api(monkeypatch):
    """Stub the job lookup; call .queue with successive return values."""
    calls = []

    def set_results(*results):
        it = iter(results)

        def fake(project, task_id):
            calls.append((project, task_id))
            value = next(it)
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(treeherder, "_job", fake)

    monkeypatch.setattr(treeherder.time, "sleep", lambda s: None)
    return type("Api", (), {"queue": staticmethod(set_results), "calls": calls})


@pytest.mark.parametrize(
    ("classification", "reason"),
    [
        (2, "fixed by commit"),
        (3, "expected fail"),
        (4, "intermittent"),
        (5, "infra"),
        (7, "autoclassified intermittent"),
        (8, "intermittent needs bugid"),
    ],
)
def test_already_judged_failures_are_skipped(classification, reason):
    assert treeherder.skip_reason(_job(classification)) == reason


@pytest.mark.parametrize("classification", [1, 6])
def test_undecided_failures_are_investigated(classification):
    # "not classified" and "new failure not classified" may still be real.
    assert treeherder.skip_reason(_job(classification)) is None


def test_unknown_classification_is_investigated():
    # A classification we do not know about must not silently drop a regression.
    assert treeherder.skip_reason(_job(99)) is None


def test_missing_job_is_investigated():
    assert treeherder.skip_reason(None) is None


def test_waits_for_ingestion_then_returns_the_job(api):
    # Treeherder ingests a minute or so after the failure message arrives.
    api.queue(None, None, _job(4))
    assert treeherder.job_for_task("autoland", "TT") == _job(4)
    assert len(api.calls) == 3


def test_never_ingested_fails_open(api, monkeypatch):
    ticks = iter([0.0, treeherder.settings.treeherder_ingest_max_wait_seconds + 1])
    monkeypatch.setattr(treeherder.time, "monotonic", lambda: next(ticks))
    api.queue(None, None)
    assert treeherder.job_for_task("autoland", "TT") is None


def test_lookup_error_fails_open(api):
    api.queue(httpx.ConnectError("treeherder down"))
    assert treeherder.job_for_task("autoland", "TT") is None


def test_lookup_uses_the_task_id_not_the_revision(monkeypatch):
    # jobs/?revision= silently ignores the filter and returns unrelated jobs.
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _response({"results": [_job(4)]})

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    assert treeherder._job("autoland", "TASK123")["failure_classification_id"] == 4
    assert "task_id=TASK123" in seen["url"]
    assert "revision=" not in seen["url"]
    assert "/project/autoland/jobs/" in seen["url"]


def test_not_ingested_returns_none(monkeypatch):
    monkeypatch.setattr(
        treeherder.httpx, "get", lambda url, **k: _response({"results": []})
    )
    assert treeherder._job("autoland", "TT") is None


@pytest.fixture(autouse=True)
def _clear_group_cache():
    caches = (
        treeherder._group_cache,
        treeherder._jobs_cache,
        treeherder._push_id_cache,
        treeherder._bug_suggestions_cache,
    )
    for cache in caches:
        cache.clear()
    yield
    for cache in caches:
        cache.clear()


def test_group_results_are_fetched_once_per_push(monkeypatch):
    # A push emits many failing tasks; they must share one fetch.
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _response({"TASK1": {"a.ini": False}, "TASK2": {"b.ini": True}})

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    first = treeherder.group_results("autoland", "abc" * 13 + "d")
    second = treeherder.group_results("autoland", "abc" * 13 + "d")
    assert first == second
    assert len(calls) == 1
    assert "group_results/?revision=" in calls[0]


def test_group_results_refresh_bypasses_the_cache(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _response({"TASK1": {"a.ini": False}})

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    rev = "abc" * 13 + "d"
    treeherder.group_results("autoland", rev)
    treeherder.group_results("autoland", rev, refresh=True)
    assert len(calls) == 2


def test_group_results_cached_per_project_and_revision(monkeypatch):
    calls = []
    monkeypatch.setattr(
        treeherder.httpx,
        "get",
        lambda url, **k: (calls.append(url), _response({}))[1],
    )
    treeherder.group_results("autoland", "a" * 40)
    treeherder.group_results("autoland", "b" * 40)
    treeherder.group_results("mozilla-central", "a" * 40)
    assert len(calls) == 3


def test_label_jobs_filters_by_push_and_label(monkeypatch):
    urls = []

    def fake_get(url, **kwargs):
        urls.append(url)
        if "/push/?revision=" in url:
            return _response({"results": [{"id": 4242}]})
        return _response(
            {"results": [{"result": "success", "state": "completed", "task_id": "T1"}]}
        )

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    jobs = treeherder.label_jobs("autoland", "a" * 40, "test-linux/opt-mochitest-1")
    assert [(j["result"], j["state"], j["task_id"]) for j in jobs] == [
        ("success", "completed", "T1")
    ]
    assert any("push_id=4242" in u for u in urls)
    # The label is URL-encoded: it contains a slash.
    assert any("job_type_name=test-linux%2Fopt-mochitest-1" in u for u in urls)


def test_label_jobs_without_a_push_is_empty(monkeypatch):
    monkeypatch.setattr(
        treeherder.httpx, "get", lambda url, **k: _response({"results": []})
    )
    assert treeherder.label_jobs("autoland", "a" * 40, "l") == []


def test_label_jobs_cached_per_push_and_label(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "/push/?revision=" in url:
            return _response({"results": [{"id": 1}]})
        return _response({"results": []})

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    rev = "a" * 40
    treeherder.label_jobs("autoland", rev, "one")
    treeherder.label_jobs("autoland", rev, "one")
    treeherder.label_jobs("autoland", rev, "two")
    # push id resolved once, jobs fetched once per distinct label.
    assert sum("/push/?revision=" in u for u in calls) == 1
    assert sum("job_type_name=" in u for u in calls) == 2


def test_absent_task_raises_rather_than_reporting_nothing(monkeypatch):
    # A task-level failure (crash, timeout) records no per-manifest results, and so
    # does a log still being parsed. Neither means "nothing failed".
    monkeypatch.setattr(
        treeherder, "group_results", lambda p, rev, refresh=False: {"OTHER": {}}
    )
    with pytest.raises(treeherder.GroupResultsUnavailable):
        treeherder.failing_groups("autoland", "a" * 40, "T1")


def test_task_present_with_no_failures_reports_nothing(monkeypatch):
    # Distinct from the case above: Treeherder has results, none of them failing.
    monkeypatch.setattr(
        treeherder,
        "group_results",
        lambda p, rev, refresh=False: {"T1": {"a.ini": True, "b.ini": True}},
    )
    assert treeherder.failing_groups("autoland", "a" * 40, "T1") == []


def test_recheck_reads_the_current_classification(monkeypatch):
    monkeypatch.setattr(treeherder, "_job", lambda p, t: _job(4))
    assert treeherder.recheck_skip_reason("autoland", "T1") == "intermittent"


def test_recheck_does_not_wait_for_ingestion(monkeypatch):
    monkeypatch.setattr(treeherder, "_job", lambda p, t: None)
    monkeypatch.setattr(
        treeherder.time, "sleep", lambda s: pytest.fail("re-check must not wait")
    )
    assert treeherder.recheck_skip_reason("autoland", "T1") is None


def test_recheck_error_does_not_drop_the_failure(monkeypatch):
    def boom(project, task_id):
        raise RuntimeError("treeherder down")

    monkeypatch.setattr(treeherder, "_job", boom)
    assert treeherder.recheck_skip_reason("autoland", "T1") is None


def test_failing_groups_returns_only_failures(monkeypatch):
    monkeypatch.setattr(
        treeherder,
        "group_results",
        lambda p, rev, refresh=False: {
            "T1": {"a.ini": False, "b.ini": True, "/": False}
        },
    )
    assert treeherder.failing_groups("autoland", "a" * 40, "T1") == ["a.ini"]


def test_failing_groups_refetches_when_task_absent(monkeypatch):
    calls = []

    def fake(project, rev, refresh=False):
        calls.append(refresh)
        return {"OTHER": {"a.ini": False}} if refresh else {}

    monkeypatch.setattr(treeherder, "group_results", fake)
    with pytest.raises(treeherder.GroupResultsUnavailable):
        treeherder.failing_groups("autoland", "a" * 40, "T1")
    assert calls == [False, True]


def test_config_jobs_filters_platform_option_client_side(monkeypatch):
    # Treeherder accepts platform_option but silently ignores it, so the query can
    # only narrow by platform and the option must be filtered here.
    urls = []

    def fake_get(url, **kwargs):
        urls.append(url)
        if "/push/?revision=" in url:
            return _response({"results": [{"id": 7}]})
        return _response(
            {
                "results": [
                    {
                        "result": "success",
                        "state": "completed",
                        "task_id": "A",
                        "platform_option": "debug",
                    },
                    {
                        "result": "testfailed",
                        "state": "completed",
                        "task_id": "B",
                        "platform_option": "opt",
                    },
                ]
            }
        )

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    jobs = treeherder.config_jobs("autoland", "a" * 40, "linux2404-64", "debug")
    assert [j["task_id"] for j in jobs] == ["A"]
    assert any("platform=linux2404-64" in u for u in urls)
    assert not any("platform_option=" in u for u in urls)


def test_config_jobs_shares_one_fetch_across_options(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "/push/?revision=" in url:
            return _response({"results": [{"id": 7}]})
        return _response({"results": []})

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    rev = "a" * 40
    treeherder.config_jobs("autoland", rev, "linux2404-64", "debug")
    treeherder.config_jobs("autoland", rev, "linux2404-64", "opt")
    assert sum("platform=" in u for u in calls) == 1


def test_await_skip_reason_returns_a_late_verdict(monkeypatch):
    verdicts = iter([_job(1), _job(4)])
    monkeypatch.setattr(treeherder, "_job", lambda p, t: next(verdicts))
    monkeypatch.setattr(treeherder.time, "sleep", lambda s: None)
    assert treeherder.await_skip_reason("autoland", "T1", _job(1)) == "intermittent"


def test_await_skip_reason_uses_the_verdict_it_was_given(monkeypatch):
    # Already classified at ingestion: no further request, and no waiting.
    def fail(*_):
        raise AssertionError("should not re-fetch an already classified job")

    monkeypatch.setattr(treeherder, "_job", fail)
    monkeypatch.setattr(treeherder.time, "sleep", fail)
    assert treeherder.await_skip_reason("autoland", "T1", _job(4)) == "intermittent"


def test_await_skip_reason_does_not_wait_without_a_job(monkeypatch):
    # Treeherder never ingested the task, so there is no verdict coming; waiting the
    # full window for one would stall the failure for nothing.
    def fail(*_):
        raise AssertionError("should not poll for a job Treeherder does not have")

    monkeypatch.setattr(treeherder, "_job", fail)
    monkeypatch.setattr(treeherder.time, "sleep", fail)
    assert treeherder.await_skip_reason("autoland", "T1", None) is None


def test_await_skip_reason_gives_up_and_investigates(monkeypatch):
    monkeypatch.setattr(treeherder, "_job", lambda p, t: _job(6))
    monkeypatch.setattr(treeherder.time, "sleep", lambda s: None)
    ticks = iter([0.0, treeherder.settings.treeherder_classification_wait_seconds + 1])
    monkeypatch.setattr(treeherder.time, "monotonic", lambda: next(ticks))
    assert treeherder.await_skip_reason("autoland", "T1", _job(6)) is None


def _bug(bug_id, resolution="", keywords="intermittent-failure,intermittent-testcase"):
    return {
        "id": bug_id,
        "status": "NEW",
        "resolution": resolution,
        "keywords": keywords,
    }


def _suggestion(search, new_in_rev, open_recent=(), all_others=()):
    return {
        "search": search,
        "failure_new_in_rev": new_in_rev,
        "bugs": {"open_recent": list(open_recent), "all_others": list(all_others)},
    }


_FAIL_LINE = "TEST-UNEXPECTED-FAIL | test_dataChannel.html | Test timed out."


@pytest.fixture
def suggestions(monkeypatch):
    """Stub the bug_suggestions fetch; call it with the entries to return."""

    def use(*entries):
        monkeypatch.setattr(
            treeherder, "bug_suggestions", lambda project, job_id: list(entries)
        )

    return use


def _failing_job():
    return {"id": 42, "task_id": "TT", "failure_classification_id": 1}


def test_known_intermittent_needs_both_signals(suggestions):
    suggestions(_suggestion(_FAIL_LINE, False, open_recent=[_bug(2016093)]))
    match = treeherder.intermittent_match("autoland", _failing_job())
    assert match.known is True
    assert match.bug_ids == [2016093]


def test_a_new_failure_with_an_intermittent_bug_still_runs(suggestions):
    # Genuine regressions also match open intermittent bugs, so the bug alone
    # must never be enough to skip.
    suggestions(_suggestion(_FAIL_LINE, True, open_recent=[_bug(1828735)]))
    match = treeherder.intermittent_match("autoland", _failing_job())
    assert match.known is False
    assert match.bug_ids == [1828735]


def test_an_old_failure_without_a_bug_still_runs(suggestions):
    suggestions(_suggestion(_FAIL_LINE, False))
    assert treeherder.intermittent_match("autoland", _failing_job()).known is False


def test_one_unknown_line_keeps_the_whole_job(suggestions):
    suggestions(
        _suggestion(_FAIL_LINE, False, open_recent=[_bug(2016093)]),
        _suggestion("TEST-UNEXPECTED-FAIL | browser_startup.js | Got 1", True),
    )
    assert treeherder.intermittent_match("autoland", _failing_job()).known is False


def test_harness_noise_does_not_decide(suggestions):
    # Harness noise matches junk bugs on nearly every job.
    suggestions(
        _suggestion("[taskcluster:error] exit status 1", False, [_bug(2034259)]),
        _suggestion(_FAIL_LINE, True),
    )
    match = treeherder.intermittent_match("autoland", _failing_job())
    assert match.known is False
    assert match.bug_ids == []


def test_resolved_bugs_are_not_evidence(suggestions):
    suggestions(
        _suggestion(_FAIL_LINE, False, all_others=[_bug(1798750, "INCOMPLETE")])
    )
    match = treeherder.intermittent_match("autoland", _failing_job())
    assert match.known is False
    assert match.bug_ids == []


def test_a_bug_without_the_keyword_is_not_evidence(suggestions):
    suggestions(_suggestion(_FAIL_LINE, False, [_bug(2055984, keywords="regression")]))
    assert treeherder.intermittent_match("autoland", _failing_job()).known is False


def test_no_suggestions_fails_open(suggestions):
    suggestions()
    assert treeherder.intermittent_match("autoland", _failing_job()).known is False


def test_a_suggestions_error_fails_open(monkeypatch):
    def boom(project, job_id):
        raise RuntimeError("treeherder down")

    monkeypatch.setattr(treeherder, "bug_suggestions", boom)
    assert treeherder.intermittent_match("autoland", _failing_job()).known is False


def test_a_job_without_an_id_fails_open(monkeypatch):
    monkeypatch.setattr(
        treeherder,
        "bug_suggestions",
        lambda *_: pytest.fail("nothing to look up without a job id"),
    )
    assert treeherder.intermittent_match("autoland", None).known is False
    assert treeherder.intermittent_match("autoland", {"task_id": "TT"}).known is False


def test_a_missing_new_in_rev_flag_is_treated_as_new(suggestions):
    entry = _suggestion(_FAIL_LINE, False, [_bug(2016093)])
    del entry["failure_new_in_rev"]
    suggestions(entry)
    assert treeherder.intermittent_match("autoland", _failing_job()).known is False


def test_bug_ids_are_deduped_across_lines(suggestions):
    suggestions(
        _suggestion(_FAIL_LINE, False, [_bug(2016093)]),
        _suggestion(_FAIL_LINE + " (retry)", False, [_bug(2016093)]),
    )
    assert treeherder.intermittent_match("autoland", _failing_job()).bug_ids == [
        2016093
    ]


def test_bug_suggestions_are_fetched_once_per_job(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _response([_suggestion(_FAIL_LINE, False)])

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    assert treeherder.bug_suggestions("autoland", 42) == treeherder.bug_suggestions(
        "autoland", 42
    )
    assert len(calls) == 1
    assert calls[0].endswith("/project/autoland/jobs/42/bug_suggestions/")


def test_push_url_points_at_the_push():
    assert treeherder.push_url("autoland", "abc123") == (
        "https://treeherder.mozilla.org/#/jobs?repo=autoland&revision=abc123"
    )


def test_job_url_selects_the_task_on_its_push():
    assert treeherder.job_url("autoland", "abc123", "TT") == (
        "https://treeherder.mozilla.org/#/jobs"
        "?repo=autoland&revision=abc123&selectedTaskRun=TT"
    )


def test_job_url_without_a_revision_still_selects_the_task():
    # Treeherder cannot preload the push, but resolves the task and links to it.
    assert treeherder.job_url("autoland", None, "TT") == (
        "https://treeherder.mozilla.org/#/jobs?repo=autoland&selectedTaskRun=TT"
    )


def _status_response(code, payload=None):
    request = httpx.Request("GET", "https://treeherder.example/api")
    return httpx.Response(code, json=payload or {}, request=request)


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    monkeypatch.setattr(treeherder._fetch.retry, "sleep", lambda s: None)


def test_a_transient_status_is_retried_then_succeeds(monkeypatch):
    responses = [_status_response(502), _status_response(200, {"ok": True})]
    monkeypatch.setattr(treeherder.httpx, "get", lambda *a, **k: responses.pop(0))
    assert treeherder._get("autoland/push/") == {"ok": True}
    assert not responses


def test_a_transient_status_raises_once_the_retries_are_spent(monkeypatch):
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return _status_response(502)

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    with pytest.raises(httpx.HTTPStatusError):
        treeherder._get("autoland/push/")
    assert len(calls) == treeherder._ATTEMPTS


def test_a_network_error_is_retried(monkeypatch):
    outcomes = [httpx.ConnectError("boom"), _status_response(200, {"ok": True})]

    def fake_get(*a, **k):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    assert treeherder._get("autoland/push/") == {"ok": True}


def test_a_real_error_is_not_retried(monkeypatch):
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return _status_response(404)

    monkeypatch.setattr(treeherder.httpx, "get", fake_get)
    with pytest.raises(httpx.HTTPStatusError):
        treeherder._get("autoland/push/")
    assert len(calls) == 1


_TEST_LINE = (
    "TEST-UNEXPECTED-FAIL | browser/base/content/test/browser_tab.js | Test timed out."
)


def test_failing_tests_reads_the_test_paths(suggestions):
    suggestions(_suggestion(_TEST_LINE, True))
    assert treeherder.failing_tests("autoland", _failing_job()) == [
        "browser/base/content/test/browser_tab.js"
    ]


def test_failing_tests_strips_the_xpcshell_manifest_prefix(suggestions):
    suggestions(
        _suggestion(
            "TEST-UNEXPECTED-FAIL | xpcshell.toml:netwerk/test/unit/test_bug.js | boom",
            True,
        )
    )
    assert treeherder.failing_tests("autoland", _failing_job()) == [
        "netwerk/test/unit/test_bug.js"
    ]


def test_failing_tests_deduplicates_repeated_lines(suggestions):
    suggestions(_suggestion(_TEST_LINE, True), _suggestion(_TEST_LINE, True))
    assert treeherder.failing_tests("autoland", _failing_job()) == [
        "browser/base/content/test/browser_tab.js"
    ]


def test_failing_tests_ignores_lines_that_are_not_failures(suggestions):
    suggestions(
        _suggestion(_TEST_LINE, True),
        _suggestion("[taskcluster:error] exit status 1", True),
    )
    assert treeherder.failing_tests("autoland", _failing_job()) == [
        "browser/base/content/test/browser_tab.js"
    ]


def test_a_failure_not_attributed_to_a_test_yields_none(suggestions):
    # "ShutdownLeaks" and "shutdown hang" name no test, so the failing tests of the
    # job cannot be enumerated -- which must not read as "only the other one failed".
    suggestions(
        _suggestion(_TEST_LINE, True),
        _suggestion("TEST-UNEXPECTED-FAIL | ShutdownLeaks | leaked 2 windows", True),
    )
    assert treeherder.failing_tests("autoland", _failing_job()) is None


def test_failing_tests_is_none_without_any_failure_line(suggestions):
    suggestions(_suggestion("[taskcluster:error] exit status 1", True))
    assert treeherder.failing_tests("autoland", _failing_job()) is None


def test_failing_tests_is_none_without_a_job(suggestions):
    suggestions(_suggestion(_TEST_LINE, True))
    assert treeherder.failing_tests("autoland", None) is None


def test_failing_tests_fails_soft_on_error(monkeypatch):
    def boom(project, job_id):
        raise RuntimeError("treeherder is down")

    monkeypatch.setattr(treeherder, "bug_suggestions", boom)
    assert treeherder.failing_tests("autoland", _failing_job()) is None
