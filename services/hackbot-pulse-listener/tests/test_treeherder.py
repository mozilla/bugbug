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
    for cache in (
        treeherder._group_cache,
        treeherder._jobs_cache,
        treeherder._push_id_cache,
    ):
        cache.clear()
    yield
    for cache in (
        treeherder._group_cache,
        treeherder._jobs_cache,
        treeherder._push_id_cache,
    ):
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
    verdicts = iter([None, None, _job(4)])
    monkeypatch.setattr(treeherder, "_job", lambda p, t: next(verdicts))
    monkeypatch.setattr(treeherder.time, "sleep", lambda s: None)
    assert treeherder.await_skip_reason("autoland", "T1") == "intermittent"


def test_await_skip_reason_gives_up_and_investigates(monkeypatch):
    monkeypatch.setattr(treeherder, "_job", lambda p, t: _job(6))
    monkeypatch.setattr(treeherder.time, "sleep", lambda s: None)
    ticks = iter([0.0, treeherder.settings.treeherder_classification_wait_seconds + 1])
    monkeypatch.setattr(treeherder.time, "monotonic", lambda: next(ticks))
    assert treeherder.await_skip_reason("autoland", "T1") is None
