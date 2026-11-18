# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from hackbot_agents.perf_regression.ci import TryClient
from hackbot_agents.perf_regression.evidence import EvidenceError, metric_key


@pytest.mark.parametrize("initial_commit", [None, ""])
async def test_waits_for_all_repetitions_and_ignores_foreign_revision(
    row, initial_commit
):
    key = metric_key(row["series_signature"])
    polls = 0
    lando_polls = 0

    async def get(url, **params):
        nonlocal polls, lando_polls
        if "landing_jobs" in url:
            lando_polls += 1
            if lando_polls == 1:
                return {"status": "SUBMITTED", "commit_id": initial_commit}
            return {"status": "LANDED", "commit_id": "b" * 40}
        if url.endswith("/push/"):
            return {"results": [{"id": 7, "revision": "b" * 40}]}
        polls += 1
        return {
            "results": [
                {
                    "id": i,
                    "job_type_name": "test-speedometer3",
                    "state": "completed",
                    "result": "success",
                }
                for i in range(10 if polls == 1 else 20)
            ]
        }

    async with httpx.AsyncClient() as http:
        ci = TryClient(
            http,
            {"metrics": [row]},
            poll_seconds=0,
            timeout_seconds=2,
        )
        ci.evidence.get = get
        ci.evidence.measurements = AsyncMock(
            return_value={
                key: [
                    {"job_id": i, "value": 100, "revision": "b" * 40} for i in range(20)
                ]
                + [{"job_id": 1, "value": 1, "revision": "different"}]
            }
        )
        progress = []
        result = await ci.wait(
            {
                "status_url": "https://lando/landing_jobs/1",
                "tasks": ["test-speedometer3"],
                "repetitions": 20,
            },
            progress.append,
        )
    assert polls == 2
    assert [p["complete"] for p in progress] == [False, True]
    assert len(result["measurements"][key]) == 20


async def test_failed_lando_job_is_not_a_green_try_push(row):
    async with httpx.AsyncClient() as http:
        ci = TryClient(http, {"metrics": [row]}, poll_seconds=0)
        ci.evidence.get = AsyncMock(return_value={"status": "FAILED"})
        with pytest.raises(EvidenceError, match="did not land"):
            await ci.wait(
                {"status_url": "https://lando/landing_jobs/1"}, lambda _: None
            )


@pytest.mark.parametrize(
    "prerequisite", ["build-linux64/opt", "signing-apk-fenix-nightly-simulation"]
)
async def test_busted_build_returns_feedback_without_waiting_for_missing_metrics(
    row, prerequisite
):
    async with httpx.AsyncClient() as http:
        ci = TryClient(http, {"metrics": [row]}, poll_seconds=0)
        ci.evidence.get = AsyncMock(
            side_effect=[
                {"status": "LANDED", "commit_id": "b" * 40},
                {"results": [{"id": 7, "revision": "b" * 40}]},
                {
                    "results": [
                        {
                            "id": 1,
                            "task_id": "failed-build",
                            "job_type_name": prerequisite,
                            "state": "completed",
                            "result": "busted",
                        }
                    ]
                },
            ]
        )
        ci.evidence.measurements = AsyncMock(return_value={})
        result = await ci.wait(
            {
                "status_url": "https://lando/landing_jobs/1",
                "tasks": ["test-speedometer3"],
                "repetitions": 1,
            },
            lambda _: None,
        )
        assert result["failure_reason"] == "CI build/test failure"
        assert result["failed_jobs"][0]["task_id"] == "failed-build"
        ci.evidence.measurements.assert_awaited_once()


async def test_missing_jobs_timeout_instead_of_claiming_success(row):
    async with httpx.AsyncClient() as http:
        ci = TryClient(http, {"metrics": [row]}, timeout_seconds=0)
        with pytest.raises(EvidenceError, match="timed out"):
            await ci.wait({}, lambda _: None)


@pytest.mark.parametrize(
    "name",
    [
        "source-test-mozlint-codespell",
        "source-test-mozlint-clang-format",
        "source-test-mozlint-eslint",
        "test-unrelated",
        "build-unrelated/opt",
        "toolchain-linux64-clang-tidy",
        "fetch-unrelated",
        "docker-image-unrelated",
    ],
)
@pytest.mark.parametrize("delayed_metrics", [False, True])
async def test_unrelated_failures_do_not_reject_measured_candidate(
    row, name, delayed_metrics
):
    key = metric_key(row["series_signature"])
    jobs = {
        "results": [
            {
                "id": 1,
                "job_type_name": name,
                "state": "completed",
                "result": "testfailed",
            },
            {
                "id": 2,
                "job_type_name": "test-perf",
                "state": "completed",
                "result": "success",
            },
        ]
    }
    points = {key: [{"job_id": 2, "revision": "b" * 40, "value": 100}]}
    async with httpx.AsyncClient() as http:
        ci = TryClient(http, {"metrics": [row]}, poll_seconds=0)
        ci.evidence.get = AsyncMock(
            side_effect=[
                {"commit_id": "b" * 40},
                {"results": [{"id": 7, "revision": "b" * 40}]},
                jobs,
                jobs,
            ]
        )
        ci.evidence.measurements = AsyncMock(
            side_effect=[{}, points] if delayed_metrics else [points]
        )
        progress = []
        result = await ci.wait(
            {
                "status_url": "https://lando/landing_jobs/1",
                "tasks": ["test-perf"],
                "repetitions": 1,
            },
            progress.append,
        )
    assert result["complete"]
    assert len(progress) == (2 if delayed_metrics else 1)
    assert all("failure_reason" not in p for p in progress)
    if name.startswith("source-test-mozlint-"):
        assert result["ignored_lint_failures"][0]["job_type_name"] == name
    else:
        assert result["ignored_lint_failures"] == []
    assert "failure_reason" not in result
    assert len(result["measurements"][key]) == 1


@pytest.mark.parametrize("state", ["pending", "running"])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("unrelated_failure", [False, True])
async def test_waits_for_prerequisites_before_missing_tests_can_settle(
    row, state, partial, unrelated_failure
):
    def job(id, name, state="completed", result="success"):
        return {"id": id, "job_type_name": name, "state": state, "result": result}

    tasks = ["test-perf-linux", "test-perf-macos"]
    tests = [job(i, tasks[i // 5]) for i in range(10)]
    other = [job(100, "Gecko Decision Task")]
    if unrelated_failure:
        other.append(job(101, "toolchain-linux64-clang-tidy", result="busted"))
    initial = (tests[:5] if partial else []) + other
    initial.append(job(102, "build-macosx/opt", state=state, result="unknown"))
    finished = tests + other + [job(102, "build-macosx/opt")]
    metrics = [
        {**row, "series_signature": {**row["series_signature"], "test": task}}
        for task in tasks
    ]
    async with httpx.AsyncClient() as http:
        ci = TryClient(
            http,
            {"metrics": metrics},
            poll_seconds=0,
            settle_seconds=0,
        )
        ci.evidence.get = AsyncMock(
            side_effect=[
                {"commit_id": "b" * 40},
                {"results": [{"id": 7, "revision": "b" * 40}]},
                {"results": initial},
                {"results": finished},
            ]
        )
        ci.evidence.measurements = AsyncMock(
            return_value={
                metric_key(metric["series_signature"]): [
                    {"job_id": j["id"], "revision": "b" * 40, "value": 100}
                    for j in tests
                    if j["job_type_name"] == task
                ]
                for metric, task in zip(metrics, tasks)
            }
        )
        progress = []
        result = await ci.wait(
            {
                "status_url": "https://lando/landing_jobs/1",
                "tasks": tasks,
                "repetitions": 5,
            },
            progress.append,
        )
    assert [p["complete"] for p in progress] == [False, True]
    assert all("failure_reason" not in p for p in progress)
    assert all(len(group) == 5 for group in result["measurements"].values())


async def test_settling_timer_resets_when_prerequisites_resume(row, monkeypatch):
    now = 0
    monkeypatch.setattr(
        "hackbot_agents.perf_regression.ci.time", SimpleNamespace(monotonic=lambda: now)
    )
    snapshots = iter(
        [(0, None), (3601, "running"), (3602, "completed"), (7203, "completed")]
    )

    async def get(url, **params):
        nonlocal now
        if "landing_jobs" in url:
            return {"commit_id": "b" * 40}
        if url.endswith("/push/"):
            return {"results": [{"id": 7, "revision": "b" * 40}]}
        now, build_state = next(snapshots)
        jobs = [
            {
                "id": 1,
                "job_type_name": "test-perf",
                "state": "completed",
                "result": "success",
            }
        ]
        if build_state:
            jobs.append(
                {
                    "id": 2,
                    "job_type_name": "build-macosx/opt",
                    "state": build_state,
                    "result": "success" if build_state == "completed" else "unknown",
                }
            )
        return {"results": jobs}

    async with httpx.AsyncClient() as http:
        ci = TryClient(http, {"metrics": [row]}, poll_seconds=0)
        ci.evidence.get = get
        ci.evidence.measurements = AsyncMock(return_value={})
        progress = []
        result = await ci.wait(
            {
                "status_url": "https://lando/landing_jobs/1",
                "tasks": ["test-perf"],
                "repetitions": 1,
            },
            progress.append,
        )
    assert len(progress) == 4
    assert all("failure_reason" not in p for p in progress[:-1])
    assert "required repetitions" in result["failure_reason"]


@pytest.mark.parametrize("failure", ["testfailed", "exception", "usercancel"])
async def test_failed_repetition_retains_other_measurements_and_waits_for_running_jobs(
    row, failure
):
    key = metric_key(row["series_signature"])

    def jobs(state):
        return {
            "results": [
                {
                    "id": i,
                    "job_type_name": "test-perf",
                    "state": "completed" if i == 0 else state,
                    "result": failure if i == 0 else "success",
                }
                for i in range(5)
            ]
        }

    async with httpx.AsyncClient() as http:
        ci = TryClient(http, {"metrics": [row]}, poll_seconds=0)
        ci.evidence.get = AsyncMock(
            side_effect=[
                {"commit_id": "b" * 40},
                {"results": [{"id": 7, "revision": "b" * 40}]},
                jobs("running"),
                jobs("completed"),
            ]
        )
        ci.evidence.measurements = AsyncMock(
            return_value={
                key: [
                    {"job_id": i, "revision": "b" * 40, "value": 100}
                    for i in range(1, 5)
                ]
            }
        )
        progress = []
        result = await ci.wait(
            {
                "status_url": "https://lando/landing_jobs/1",
                "tasks": ["test-perf"],
                "repetitions": 5,
            },
            progress.append,
        )
    assert "failure_reason" not in progress[0]
    assert result["failure_reason"] == "CI build/test failure"
    assert result["failed_jobs"][0]["result"] == failure
    assert not result["complete"]
    assert len(result["measurements"][key]) == 4


@pytest.mark.parametrize("name", ["Gecko Decision Task", "toolchain-linux64-clang"])
async def test_prerequisite_failure_stops_before_tests_are_scheduled(row, name):
    async with httpx.AsyncClient() as http:
        ci = TryClient(http, {"metrics": [row]}, poll_seconds=0)
        ci.evidence.get = AsyncMock(
            side_effect=[
                {"commit_id": "b" * 40},
                {"results": [{"id": 7, "revision": "b" * 40}]},
                {
                    "results": [
                        {
                            "id": 1,
                            "job_type_name": name,
                            "state": "completed",
                            "result": "busted",
                        }
                    ]
                },
            ]
        )
        ci.evidence.measurements = AsyncMock(return_value={})
        result = await ci.wait(
            {
                "status_url": "https://lando/landing_jobs/1",
                "tasks": ["test-perf"],
                "repetitions": 5,
            },
            lambda _: None,
        )
    assert result["failure_reason"] == "CI build/test failure"
    assert result["failed_jobs"][0]["job_type_name"] == name


async def test_finished_jobs_with_missing_metrics_report_partial_data(row):
    key = metric_key(row["series_signature"])
    jobs = {
        "results": [
            {
                "id": i,
                "job_type_name": "test-perf",
                "state": "completed",
                "result": "success",
            }
            for i in range(5)
        ]
    }
    async with httpx.AsyncClient() as http:
        ci = TryClient(
            http,
            {"metrics": [row]},
            poll_seconds=0,
            settle_seconds=0,
        )
        ci.evidence.get = AsyncMock(
            side_effect=[
                {"commit_id": "b" * 40},
                {"results": [{"id": 7, "revision": "b" * 40}]},
                jobs,
            ]
        )
        ci.evidence.measurements = AsyncMock(
            return_value={
                key: [
                    {"job_id": i, "revision": "b" * 40, "value": 100} for i in range(3)
                ]
            }
        )
        progress = []
        result = await ci.wait(
            {
                "status_url": "https://lando/landing_jobs/1",
                "tasks": ["test-perf"],
                "repetitions": 5,
            },
            progress.append,
        )
    assert len(progress) == 1
    assert "required repetitions" in result["failure_reason"]
    assert not result["complete"]
    assert len(result["measurements"][key]) == 3


async def test_timeout_keeps_partial_measurements(row):
    key = metric_key(row["series_signature"])
    async with httpx.AsyncClient() as http:
        ci = TryClient(
            http,
            {"metrics": [row]},
            poll_seconds=0,
            timeout_seconds=0.05,
        )
        responses = [
            {"commit_id": "b" * 40},
            {"results": [{"id": 7, "revision": "b" * 40}]},
        ]

        async def get(url, **params):
            if responses:
                return responses.pop(0)
            return {
                "results": [
                    {
                        "id": i,
                        "job_type_name": "test-perf",
                        "state": "running" if i else "completed",
                        "result": "unknown" if i else "success",
                    }
                    for i in range(5)
                ]
            }

        ci.evidence.get = get
        ci.evidence.measurements = AsyncMock(
            return_value={key: [{"job_id": 0, "revision": "b" * 40, "value": 100}]}
        )
        result = await ci.wait(
            {
                "status_url": "https://lando/landing_jobs/1",
                "tasks": ["test-perf"],
                "repetitions": 5,
            },
            lambda _: None,
        )
    assert "timed out" in result["failure_reason"]
    assert len(result["measurements"][key]) == 1


async def test_settling_grace_survives_separate_validation_executions(row):
    async def get(url, **params):
        if "landing_jobs" in url:
            return {"commit_id": "b" * 40}
        if url.endswith("/push/"):
            return {"results": [{"id": 7, "revision": "b" * 40}]}
        return {
            "results": [
                {
                    "id": 1,
                    "job_type_name": "test-perf",
                    "state": "completed",
                    "result": "success",
                }
            ]
        }

    submission = {
        "status_url": "https://lando/landing_jobs/1",
        "tasks": ["test-perf"],
        "repetitions": 1,
    }
    async with httpx.AsyncClient() as http:
        first = TryClient(http, {"metrics": [row]})
        first.evidence.get = get
        first.evidence.measurements = AsyncMock(return_value={})
        saved = await first.check(submission, clock=lambda: 100)
        assert saved["settled_at"] == 100
        assert not saved.get("failure_reason")
        second = TryClient(http, {"metrics": [row]})
        second.evidence.get = get
        second.evidence.measurements = AsyncMock(return_value={})
        result = await second.check(submission, saved, clock=lambda: 3701)
        assert "without every selected task and metric" in result["failure_reason"]


async def test_report_links_the_candidate_as_pushed(row):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "revision": "t" * 40,
                        "revisions": [{"revision": "t" * 40}, {"revision": "c" * 40}],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        ci = TryClient(http, {"metrics": [row]})
        assert await ci.pushed_candidate("t" * 40) == (
            "https://hg.mozilla.org/try/rev/" + "c" * 40
        )
