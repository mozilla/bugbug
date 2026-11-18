# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import httpx
import pytest
from hackbot_agents.perf_regression.evidence import (
    EvidenceClient,
    EvidenceError,
    OutOfScope,
    find_alert_ids,
    metric_key,
    regression_rows,
)


@pytest.fixture
def profile_task(monkeypatch):
    def label(row):
        sig = row["series_signature"]
        return f"test-{sig['machine_platform']}/opt-browsertime-benchmark-firefox-{sig['suite']}"

    async def tasks(self, alert, rows=None):
        return [label((rows or alert["alerts"])[0])]

    monkeypatch.setattr(EvidenceClient, "tasks", tasks)
    return label


# Autoland push IDs are not consecutive: other repositories share the counter.
TARGET = 100
BEFORE = 97
PUSHES = [
    {
        "id": TARGET + 3 * k,
        "push_timestamp": 1_000_000 + 60 * k,
        "revision": f"{k:040x}",
    }
    for k in range(-40, 40)
]


def treeherder(jobs=(), task=None, pushes=PUSHES):
    """A fake Treeherder/Taskcluster read API for profile discovery."""

    async def get(url, **params):
        if "/queue/v1/task/" in url:
            return task(url) if callable(task) else task
        if url.endswith("/push/"):
            if "id" in params:
                return {"results": [p for p in pushes if p["id"] == params["id"]]}
            if "push_timestamp__lt" in params:
                found = [
                    p
                    for p in pushes
                    if p["push_timestamp"] < params["push_timestamp__lt"]
                ]
            else:
                found = [
                    p
                    for p in pushes
                    if params["push_timestamp__gte"]
                    <= p["push_timestamp"]
                    <= params["push_timestamp__lte"]
                ]
            found.sort(key=lambda p: p["push_timestamp"], reverse=True)
            return {"results": found[: params["count"]]}
        assert url.endswith("/jobs/")
        ids = {int(i) for i in params["push_id__in"].split(",")}
        matching = [j for j in jobs if j["push_id"] in ids]
        offset = params.get("offset", 0)
        return {"results": matching[offset : offset + params["count"]]}

    return get


def test_alert_discovery_ignores_unrelated_bug_ids():
    comments = [
        {
            "text": "Bug 2067007 caused this. https://treeherder.mozilla.org/perfherder/alerts?id=53155\n./mach try perf --alert 53155"
        }
    ]
    assert find_alert_ids(comments) == [53155]
    assert (
        find_alert_ids(
            [{"text": "https://bugzilla.mozilla.org/show_bug.cgi?id=2075214"}]
        )
        == []
    )


def test_related_alert_rows_included_and_duplicates_removed(row):
    related = {**row, "status": 2}
    assert regression_rows({"alerts": [row], "related_alerts": [related]}) == [row]
    assert regression_rows({"alerts": [], "related_alerts": [related]}) == [related]
    with pytest.raises(EvidenceError):
        regression_rows({"alerts": [{**row, "is_regression": False}]})


@pytest.mark.parametrize("unit", ["ms", None])
async def test_data_matches_signature_fields_and_dedupes_ci_jobs(row, unit):
    row["series_signature"]["measurement_unit"] = unit
    sig = {**row["series_signature"], "id": 10}
    if unit is None:
        # Alert signatures serialize a null unit; the signatures API omits it.
        del sig["measurement_unit"]

    # Signature IDs and hashes may differ between try and autoland.
    def handler(request):
        if request.url.path.endswith("/data/"):
            assert request.url.params["push_id"] == "7"
            return httpx.Response(
                200,
                json={
                    "try-hash": [
                        {"signature_id": 10, "job_id": 1, "push_id": 7, "value": 100},
                        {"signature_id": 10, "job_id": 1, "push_id": 7, "value": 100},
                        {"signature_id": 10, "job_id": 2, "push_id": 8, "value": 100},
                    ]
                },
            )
        assert request.url.params.get_list("id") == ["10"]
        return httpx.Response(200, json={"10": sig})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await EvidenceClient(http).measurements("try", 7, [row])
    assert len(result[metric_key(sig)]) == 1


async def test_profile_fallback_keeps_run_id_and_does_not_extract_zip(tmp_path):
    requests = []

    def handler(request):
        requests.append(str(request.url))
        if request.url.path.endswith(".gz"):
            return httpx.Response(404)
        return httpx.Response(200, stream=httpx.ByteStream(b"zip bytes"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await EvidenceClient(http).download_profile("A" * 22, 2, tmp_path)
    assert all("/runs/2/artifacts/" in url for url in requests)
    assert result["path"].endswith(".zip")


async def test_alert_must_belong_to_input_bug(row):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"bug_number": 999})
        )
    ) as http:
        with pytest.raises(EvidenceError, match="does not belong"):
            await EvidenceClient(http).alert(53155, 2075214)


@pytest.mark.parametrize("mode", ["normal", "transient", "inaccessible"])
async def test_bug_evidence_uses_shared_reads_and_retains_retries(mode, monkeypatch):
    from unittest.mock import AsyncMock

    from hackbot_agents.perf_regression import evidence

    bug_calls = 0
    sleep = AsyncMock()
    monkeypatch.setattr(evidence.asyncio, "sleep", sleep)
    bug = {"id": 1, "regressed_by": [2], "keywords": ["perf-alert", "regression"]}
    comments = [{"text": "Performance alert"}]
    attachments = [{"id": 3, "file_name": "patch.diff"}]

    def handler(request):
        nonlocal bug_calls
        assert request.method == "GET"
        if request.url.path == "/rest/bug":
            bug_calls += 1
            assert request.url.params["id"] == "1"
            assert "regressed_by" in request.url.params["include_fields"]
            if mode == "transient" and bug_calls == 1:
                return httpx.Response(503)
            return httpx.Response(
                200, json={"bugs": [] if mode == "inaccessible" else [bug]}
            )
        if request.url.path.endswith("/comment"):
            return httpx.Response(200, json={"bugs": {"1": {"comments": comments}}})
        assert request.url.path.endswith("/attachment")
        assert request.url.params["exclude_fields"] == "data"
        return httpx.Response(200, json={"bugs": {"1": attachments}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        collector = EvidenceClient(http)
        if mode == "inaccessible":
            with pytest.raises(EvidenceError, match="Public Bugzilla bug 1"):
                await collector.bug(1)
        else:
            assert await collector.bug(1) == {
                "bug": bug,
                "comments": comments,
                "attachments": attachments,
            }
        assert not http.is_closed
    assert bug_calls == (2 if mode == "transient" else 1)
    assert sleep.await_count == (1 if mode == "transient" else 0)


@pytest.mark.parametrize("configuration", ["extra_options", "application"])
@pytest.mark.parametrize(
    "push_files,allow_harness",
    [
        ([["layout.cpp"]], False),
        ([["testing/raptor/browsertime/speedometer3.js"]], True),
        (
            [
                [
                    "taskcluster/ci/test/raptor.yml",
                    ".taskcluster.yml",
                    "tools/tryselect/perf.py",
                ]
            ],
            True,
        ),
        ([["testing/raptor/harness.py", "js/src/gc/GC.cpp"]], False),
        ([["testing/raptor/harness.py"], ["js/src/gc/GC.cpp"]], False),
        ([["testing/raptor/harness.py"], ["taskcluster/ci/test/raptor.yml"]], True),
        ([[None]], False),
        ([[]], False),
        ([None], False),
    ],
)
async def test_collector_records_culprit_scope_and_full_evidence(
    tmp_path, row, monkeypatch, configuration, push_files, allow_harness
):
    from copy import deepcopy
    from unittest.mock import AsyncMock

    revision = "b" * 40
    second = deepcopy(row)
    if configuration == "extra_options":
        second["series_signature"]["extra_options"].append("no-nv")
    else:
        second["series_signature"]["application"] = "chrome"
    same_configuration = deepcopy(row)
    same_configuration["series_signature"]["test"] = "Another/total"
    # Agent-facing VCS tools normally truncate at 60 KB. Evidence must not.
    patch = "diff --git a/layout.cpp b/layout.cpp\n" + "+source line\n" * 10_000
    revisions = [f"{i + 1:040x}" for i in range(len(push_files) - 1)] + [revision]
    changed_files = dict(zip(revisions, push_files))

    def handler(request):
        if request.url.path.endswith("/json-pushes"):
            return httpx.Response(
                200, json={"pushes": {"2": {"changesets": revisions}}}
            )
        if "/raw-rev/" in request.url.path:
            assert request.url.path.rsplit("/", 1)[-1] in revisions
            assert request.headers["User-Agent"] == "Hackbot-Perf-Regression/0.1"
            return httpx.Response(200, text=patch)
        if "/json-rev/" in request.url.path:
            node = request.url.path.rsplit("/", 1)[-1]
            files = changed_files[node]
            if files is None:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "node": node,
                    "files": [{"file": p, "status": "modified"} for p in files],
                },
            )
        assert request.url.path.endswith("/performance/data/")
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        collector = EvidenceClient(http)
        monkeypatch.setattr(
            collector, "bug", AsyncMock(return_value={"bug": {}, "comments": []})
        )
        monkeypatch.setattr(
            collector,
            "alert",
            AsyncMock(
                return_value={
                    "repository": "autoland",
                    "revision": revision,
                    "prev_push_revision": "a" * 40,
                    "push_timestamp": 1_800_000_000,
                    "alerts": [row, second, same_configuration],
                }
            ),
        )
        monkeypatch.setattr(
            collector, "git_revision", AsyncMock(side_effect=["a" * 40, revision])
        )
        monkeypatch.setattr(
            collector, "profile_pair", AsyncMock(side_effect=EvidenceError("missing"))
        )
        second["id"], same_configuration["id"] = 265001, 265002
        monkeypatch.setattr(collector, "history", AsyncMock(return_value={}))
        monkeypatch.setattr(
            collector,
            "select",
            AsyncMock(
                return_value=(
                    [row, second, same_configuration],
                    ["test-speedometer3"],
                    [],
                )
            ),
        )
        manifest = await collector.collect(1, 2, tmp_path)
        assert manifest["metric_row_ids"] == [265000, 265001, 265002]
        assert manifest["harness_changes_allowed"] is allow_harness
        assert manifest["culprit_files"] == sorted(
            {p for group in push_files if group for p in group if isinstance(p, str)}
        )
        assert collector.profile_pair.await_count == 2
        assert not http.is_closed
    for node in revisions:
        assert (tmp_path / f"culprit-{node}.patch").read_text() == patch
        assert (tmp_path / f"culprit-{node}.json").exists()


async def test_merge_push_and_private_regressor_do_not_abort_collection(
    tmp_path, row, monkeypatch
):
    from unittest.mock import AsyncMock

    from agent_tools.registry import ToolError

    revision = "b" * 40
    merge = [f"{i + 1:040x}" for i in range(120)]

    def handler(request):
        if request.url.path.endswith("/json-pushes"):
            return httpx.Response(200, json={"pushes": {"2": {"changesets": merge}}})
        assert "/raw-rev/" not in request.url.path
        assert request.url.path.endswith("/performance/data/")
        return httpx.Response(200, json={})

    async def bug(bug_id):
        if bug_id == 2:
            raise ToolError("Access denied", payload={"error": "access_denied"})
        return {"bug": {"regressed_by": [2]}, "comments": []}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        collector = EvidenceClient(http)
        monkeypatch.setattr(collector, "bug", bug)
        monkeypatch.setattr(
            collector,
            "alert",
            AsyncMock(
                return_value={
                    "repository": "mozilla-central",
                    "revision": revision,
                    "prev_push_revision": "a" * 40,
                    "push_timestamp": 1_800_000_000,
                    "alerts": [row],
                }
            ),
        )
        monkeypatch.setattr(
            collector, "git_revision", AsyncMock(side_effect=["a" * 40, revision])
        )
        monkeypatch.setattr(
            collector, "profile_pair", AsyncMock(side_effect=EvidenceError("missing"))
        )
        monkeypatch.setattr(collector, "history", AsyncMock(return_value={}))
        monkeypatch.setattr(
            collector,
            "select",
            AsyncMock(return_value=([row], ["test-speedometer3"], [])),
        )
        manifest = await collector.collect(1, 3, tmp_path)
    assert "120 changesets" in manifest["culprit_push_skipped"]
    assert manifest["harness_changes_allowed"] is False
    assert manifest["culprit_files"] == []
    assert "Access denied" in (tmp_path / "culprit-2.json").read_text()


@pytest.mark.parametrize("native_available", [True, False])
async def test_profiles_require_native_jobs_even_when_measurement_tasks_exist(
    tmp_path, row, monkeypatch, native_available, profile_task
):
    from unittest.mock import AsyncMock

    platform = row["series_signature"]["machine_platform"]
    jobs = (
        [
            {
                "id": push,
                "push_id": push,
                "platform": platform,
                "state": "completed",
                "result": "success",
                "job_type_name": profile_task(row) + "-native-profiling",
                "task_id": task,
                "retry_id": 2,
            }
            for push, task in [(BEFORE, "native-before"), (TARGET, "native-after")]
        ]
        if native_available
        else []
    )
    jobs.append(
        {
            "id": 101,
            "push_id": TARGET,
            "platform": platform,
            "state": "completed",
            "result": "success",
            "job_type_name": profile_task(row),
            "task_id": "measurement",
        }
    )
    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        monkeypatch.setattr(client, "get", treeherder(jobs))
        download = AsyncMock(return_value={"path": "native.zip"})
        monkeypatch.setattr(client, "download_profile", download)
        row = {
            **row,
            "prev_taskcluster_metadata": {"task_id": "regular-before"},
            "taskcluster_metadata": {"task_id": "regular-after"},
        }
        alert = {"repository": "autoland", "push_id": TARGET}
        if native_available:
            result = await client.profile_pair(alert, row, tmp_path)
            assert result["before"]["job"]["task_id"] == "native-before"
            assert result["after"]["job"]["task_id"] == "native-after"
            assert result["before"]["push_distance"] == -1
            assert result["after"]["push_distance"] == 0
            assert [call.args[:2] for call in download.await_args_list] == [
                ("native-before", 2),
                ("native-after", 2),
            ]
        else:
            with pytest.raises(
                EvidenceError, match="No successful before/after native"
            ):
                await client.profile_pair(alert, row, tmp_path)
            download.assert_not_awaited()


async def test_profile_search_walks_thirty_autoland_pushes_per_side(
    tmp_path, row, profile_task
):
    from unittest.mock import AsyncMock

    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = AsyncMock(side_effect=treeherder())
        client.download_profile = AsyncMock()
        with pytest.raises(EvidenceError):
            await client.profile_pair(
                {"repository": "autoland", "push_id": TARGET}, row, tmp_path
            )
        job_calls = [
            call.kwargs
            for call in client.get.await_args_list
            if call.args[0].endswith("/jobs/")
        ]
        assert len(job_calls) == 2
        before, after = (
            [int(i) for i in c["push_id__in"].split(",")] for c in job_calls
        )
        assert before == [TARGET - 3 * k for k in range(1, 31)]
        assert after == [TARGET + 3 * k for k in range(30)]
        assert all(c["state"] == "completed" for c in job_calls)
        assert all(c["result"] == "success" for c in job_calls)
        client.download_profile.assert_not_awaited()


async def test_profile_job_pagination_is_bounded(tmp_path, row, profile_task):
    from unittest.mock import AsyncMock

    jobs = [
        {"id": i, "push_id": TARGET, "platform": "unrelated"} for i in range(30_000)
    ] + [{"id": i, "push_id": BEFORE, "platform": "unrelated"} for i in range(30_000)]
    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = AsyncMock(side_effect=treeherder(jobs))
        with pytest.raises(EvidenceError):
            await client.profile_pair(
                {"repository": "autoland", "push_id": TARGET}, row, tmp_path
            )
        offsets = [
            call.kwargs["offset"]
            for call in client.get.await_args_list
            if call.args[0].endswith("/jobs/")
        ]
        assert offsets == [2000 * page for page in range(10)] * 2


def test_regression_rows_skip_alerts_attributed_elsewhere_or_invalid(row):
    def alert_row(test, pct, status):
        return {
            **row,
            "amount_pct": pct,
            "status": status,
            "series_signature": {**row["series_signature"], "test": test},
        }

    rows = regression_rows(
        {
            "alerts": [
                alert_row("own", 1, 0),
                alert_row("downstream", 9, 1),
                alert_row("reassigned-away", 9, 2),
                alert_row("invalid", 9, 3),
                alert_row("infra", 9, 5),
            ],
            "related_alerts": [
                alert_row("reassigned-here", 2, 2),
                alert_row("downstream-elsewhere", 9, 1),
            ],
        }
    )
    assert [r["series_signature"]["test"] for r in rows] == [
        "reassigned-here",
        "own",
    ]


def test_empty_extra_options_match_signature_api(row):
    alert_side = {**row["series_signature"], "extra_options": [""]}
    api_side = {**row["series_signature"], "extra_options": []}
    assert metric_key(alert_side) == metric_key(api_side)


def test_regression_rows_include_multiple_performance_suites(row):
    from copy import deepcopy

    other = deepcopy(row)
    other["series_signature"]["suite"] = "jetstream3"
    assert len(regression_rows({"alerts": [row, other]})) == 2


def test_three_largest_percentages_selected_after_deduplication(row):
    from copy import deepcopy

    rows = []
    for index, percent in enumerate([10, 90, 30, 5, 70]):
        metric = deepcopy(row)
        metric["amount_pct"] = percent
        metric["amount_abs"] = 1000 - index
        metric["series_signature"]["test"] = str(index)
        rows.append(metric)
    # Absolute changes and score direction must not alter percentage ordering.
    rows[1]["series_signature"].update(lower_is_better=False, measurement_unit="score")
    duplicate = {**rows[1], "amount_pct": 80}
    improvement = {**rows[0], "is_regression": False, "amount_pct": 2000}
    assert regression_rows(
        {"alerts": rows, "related_alerts": [duplicate, improvement]}
    ) == [rows[1], rows[4], rows[2]]


def test_equal_regression_percentages_have_stable_selection(row):
    from copy import deepcopy

    rows = []
    for name in ["d", "c", "b", "a"]:
        metric = deepcopy(row)
        metric["amount_pct"] = 10
        metric["series_signature"]["test"] = name
        rows.append(metric)
    selected = regression_rows({"alerts": rows})
    assert selected == regression_rows({"alerts": list(reversed(rows))})
    assert [r["series_signature"]["test"] for r in selected] == ["a", "b", "c"]


def test_android_regressions_are_investigated(row):
    from copy import deepcopy

    android = deepcopy(row)
    android["id"] = 265001
    android["series_signature"]["machine_platform"] = (
        "android-hw-a55-14-0-aarch64-shippable"
    )
    android["amount_pct"] = 9999
    assert regression_rows({"alerts": [row], "related_alerts": []}) == [row]
    assert regression_rows({"alerts": [row, android]}) == [android, row]


def test_alert_without_regressions_is_out_of_scope(row):
    row["is_regression"] = False
    with pytest.raises(OutOfScope, match="no regression measurements"):
        regression_rows({"alerts": [row]})


async def test_android_metric_resolves_its_own_task(row):
    from unittest.mock import AsyncMock

    row["series_signature"]["machine_platform"] = (
        "android-hw-a55-14-0-aarch64-shippable"
    )
    row["taskcluster_metadata"] = {"task_id": "a55"}
    label = "test-android-hw-a55-14-0-aarch64-shippable/opt-browsertime-benchmark-speedometer3-mobile-fenix"

    def get(url, **params):
        if url.endswith("/alertsummary-tasks/"):
            return {"tasks": [label, f"{label}-native-profiling"]}
        return {"metadata": {"name": label}}

    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = AsyncMock(side_effect=get)
        assert await client.tasks({"id": 1}, [row]) == [label]


async def test_try_tasks_are_not_speedometer_specific(row):
    from copy import deepcopy
    from unittest.mock import AsyncMock

    other = deepcopy(row)
    other["id"] = 265001
    other["series_signature"]["suite"] = "jetstream3"
    other["taskcluster_metadata"] = {"task_id": "jetstream"}
    row["taskcluster_metadata"] = {"task_id": "speedometer"}
    names = {
        "speedometer": "test-linux2404-64-shippable/opt-speedometer3",
        "jetstream": "test-linux2404-64-shippable/opt-jetstream3",
    }

    def get(url, **params):
        if url.endswith("/alertsummary-tasks/"):
            return {
                "tasks": [
                    *names.values(),
                    "test-linux2404-64-shippable/opt-jetstream3-profiling",
                    "test-android-hw-a55-14-0-aarch64-shippable/opt-browsertime-tp6m",
                    "test-linux2404-64-shippable/opt-unrelated-test",
                ]
            }
        return {"metadata": {"name": names[url.rsplit("/", 1)[-1]]}}

    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = AsyncMock(side_effect=get)
        assert await client.tasks({"id": 1}, [row, other]) == sorted(names.values())


async def test_large_alert_schedules_only_top_three_metrics_jobs(row):
    from copy import deepcopy

    tasks = [f"test-linux2404-64-shippable/opt-talos-{i:02d}" for i in range(82)]
    rows = []
    for index, percent in enumerate([10, 90, 30, 5, 70]):
        metric = deepcopy(row)
        metric["amount_pct"] = percent
        metric["id"] = 265000 + index
        metric["series_signature"]["test"] = str(index)
        metric["taskcluster_metadata"] = {"task_id": str(index)}
        rows.append(metric)
    seen = []

    def handler(request):
        if request.url.path.endswith("/alertsummary-tasks/"):
            return httpx.Response(200, json={"tasks": tasks})
        index = int(request.url.path.rsplit("/", 1)[-1])
        seen.append(index)
        return httpx.Response(200, json={"metadata": {"name": tasks[index]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        alert = {"id": 53403, "alerts": rows}
        assert await EvidenceClient(http).tasks(alert, regression_rows(alert)) == [
            tasks[1],
            tasks[2],
            tasks[4],
        ]
    assert seen == [1, 4, 2]


async def test_selected_metrics_sharing_one_ci_task_do_not_duplicate_jobs(row):
    from copy import deepcopy
    from unittest.mock import AsyncMock

    rows = []
    for name in ["fcp", "loadtime", "SpeedIndex"]:
        metric = deepcopy(row)
        metric["id"] = 265000 + len(rows)
        metric["series_signature"]["test"] = name
        metric["taskcluster_metadata"] = {"task_id": "same-task"}
        rows.append(metric)
    task = "test-linux2404-64-shippable/opt-browsertime-google-docs"

    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = AsyncMock(
            side_effect=[{"tasks": [task]}, {"metadata": {"name": task}}]
        )
        assert await client.tasks({"id": 1}, rows) == [task]
        assert client.get.await_count == 2


@pytest.mark.parametrize("expired_metadata", [False, True])
async def test_task_selection_falls_back_to_exact_historical_jobs(
    row, expired_metadata
):
    task = "test-linux2404-64-shippable/opt-browsertime-google-docs"
    before = "test-linux2404-64-shippable/opt-before"
    other_platform = "test-windows11-64-shippable/opt-browsertime-google-docs"
    signature_id = row["series_signature"]["id"]
    if expired_metadata:
        row["taskcluster_metadata"] = {"task_id": "expired"}
    else:
        row["taskcluster_metadata"] = None
    job_requests = []

    def handler(request):
        path = request.url.path
        if path.endswith("/alertsummary-tasks/"):
            return httpx.Response(200, json={"tasks": [task, before, other_platform]})
        if path.endswith("/task/expired"):
            return httpx.Response(404)
        if path.endswith("/performance/data/"):
            assert request.url.params.get_list("signature_id") == [str(signature_id)]
            assert request.url.params["start_date"] < request.url.params["end_date"]

            def point(signature, push, job):
                return {
                    "signature_id": signature,
                    "push_id": push,
                    "push_timestamp": 1_800_000_000 + (push - 100) * 60,
                    "job_id": job,
                }

            # Nearest pushes first: the alert push, then its neighbours.
            return httpx.Response(
                200,
                json={
                    "signature": [
                        point(signature_id, 97, 1),
                        point(signature_id, 100, 2),
                        point(signature_id, 100, 3),
                        point(signature_id, 104, 4),
                        point(999, 100, 5),
                    ]
                },
            )
        job = int(path.strip("/").rsplit("/", 1)[-1])
        job_requests.append(job)
        assert job in {2, 3}
        return httpx.Response(
            200, json={"job_type_name": other_platform if job == 2 else task}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        selected = await EvidenceClient(http).tasks(
            {
                "id": 1,
                "repository": "autoland",
                "push_id": 100,
                "prev_push_id": 99,
                "push_timestamp": 1_800_000_000,
                "alerts": [row],
            },
            [row],
        )
        assert selected == [task]
    assert job_requests == [2, 3]


async def test_unresolved_selected_metric_does_not_expand_to_all_alert_tasks(row):
    def handler(request):
        if request.url.path.endswith("/alertsummary-tasks/"):
            return httpx.Response(
                200, json={"tasks": ["test-linux2404-64-shippable/opt-unrelated"]}
            )
        assert request.url.path.endswith("/performance/data/")
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EvidenceError, match="No non-profiling task"):
            await EvidenceClient(http).tasks(
                {
                    "id": 1,
                    "repository": "autoland",
                    "push_id": 100,
                    "prev_push_id": 99,
                    "push_timestamp": 1_800_000_000,
                    "alerts": [row],
                },
                [row],
            )


async def test_invalid_task_selection_stops_before_profile_collection(tmp_path, row):
    from unittest.mock import AsyncMock

    async with httpx.AsyncClient() as http:
        collector = EvidenceClient(http)
        collector.bug = AsyncMock(return_value={"bug": {}, "comments": []})
        collector.alert = AsyncMock(return_value={"alerts": [row]})
        collector.history = AsyncMock(return_value={})
        collector.select = AsyncMock(side_effect=EvidenceError("No tasks"))
        collector.git_revision = AsyncMock()
        collector.profile_pair = AsyncMock()
        with pytest.raises(EvidenceError, match="No tasks"):
            await collector.collect(1, 2, tmp_path)
        collector.git_revision.assert_not_awaited()
        collector.profile_pair.assert_not_awaited()


@pytest.mark.parametrize(
    "tasks",
    [[], ["test-linux-talos-profiling"], ["test-android-hw-talos"]],
)
async def test_empty_task_selection_is_rejected(tasks, row):
    def handler(request):
        if request.url.path.endswith("/alertsummary-tasks/"):
            return httpx.Response(200, json={"tasks": tasks})
        return httpx.Response(200, json={})

    alert = {
        "id": 1,
        "repository": "autoland",
        "push_timestamp": 1_800_000_000,
        "alerts": [row],
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(EvidenceError, match="No non-profiling task"):
            await EvidenceClient(http).tasks(alert, [row])


async def test_generic_profile_discovers_paginated_artifacts(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/artifacts"):
            if not request.url.params.get("continuationToken"):
                return httpx.Response(
                    200,
                    json={
                        "artifacts": [
                            {"name": "public/test_info/profile_resource-usage.json"}
                        ],
                        "continuationToken": "next",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "artifacts": [
                        {"name": "public/test_info/profile_jetstream3.json.gz"}
                    ]
                },
            )
        return httpx.Response(200, stream=httpx.ByteStream(b"compressed profile"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        profile = await EvidenceClient(http).download_profile(
            "A" * 22, 1, tmp_path, suite="jetstream3"
        )
    assert profile["url"].endswith("profile_jetstream3.json.gz")
    assert len(calls) == 3
    assert "continuationToken=next" in calls[1]


@pytest.mark.parametrize("after_revision", ["b" * 40, "c" * 40])
async def test_other_suite_records_actual_task_revisions(
    tmp_path, row, profile_task, after_revision
):
    from unittest.mock import AsyncMock

    row["series_signature"]["suite"] = "jetstream3"
    row["prev_taskcluster_metadata"] = {"task_id": "A" * 22, "retry_id": 2}
    row["taskcluster_metadata"] = {"task_id": "B" * 22, "retry_id": 1}

    def task(url):
        return {
            "metadata": {"name": profile_task(row)},
            "payload": {
                "env": {
                    "GECKO_HEAD_REPOSITORY": "https://hg.mozilla.org/integration/autoland",
                    "GECKO_HEAD_REV": "a" * 40
                    if url.endswith("A" * 22)
                    else after_revision,
                }
            },
        }

    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.download_profile = AsyncMock(return_value={"path": "profile.json.gz"})
        client.get = treeherder(task=task)
        pair = await client.profile_pair(
            {
                "repository": "autoland",
                "push_id": TARGET,
                "prev_push_revision": "a" * 40,
                "revision": "b" * 40,
            },
            row,
            tmp_path,
        )
        assert pair["suite"] == "jetstream3"
        assert pair["nearby"] == (after_revision != "b" * 40)
        assert (
            pair["before"]["revision"]
            == pair["before"]["expected_revision"]
            == "a" * 40
        )
        assert pair["after"]["revision"] == after_revision
        assert pair["after"]["expected_revision"] == "b" * 40
        client.download_profile.assert_any_await(
            "A" * 22, 2, tmp_path, suite="jetstream3"
        )
        client.download_profile.assert_any_await(
            "B" * 22, 1, tmp_path, suite="jetstream3"
        )


@pytest.mark.parametrize("native_available", [True, False])
async def test_other_suite_prefers_native_pair_before_gecko_fallback(
    tmp_path, row, native_available, profile_task
):
    from unittest.mock import AsyncMock

    row["series_signature"]["suite"] = "jetstream3"
    row["prev_taskcluster_metadata"] = {"task_id": "A" * 22}
    row["taskcluster_metadata"] = {"task_id": "B" * 22}
    platform = row["series_signature"]["machine_platform"]
    jobs = [
        {
            "id": n,
            "push_id": n,
            "platform": platform,
            "job_type_name": profile_task(row) + "-native-profiling",
            "state": "completed",
            "result": "success",
            "task_id": f"native-{n}",
        }
        for n in (BEFORE, TARGET)
    ]

    async def download(task, retry, dest, **kwargs):
        assert kwargs["suite"] == "jetstream3"
        if task.startswith("native") and not native_available:
            raise EvidenceError("expired")
        return {"task_id": task}

    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = treeherder(
            jobs,
            task={
                "metadata": {"name": profile_task(row)},
                "payload": {
                    "env": {
                        "GECKO_HEAD_REPOSITORY": "https://hg.mozilla.org/integration/autoland",
                        "GECKO_HEAD_REV": "a" * 40,
                    }
                },
            },
        )
        client.download_profile = AsyncMock(side_effect=download)
        pair = await client.profile_pair(
            {"repository": "autoland", "push_id": TARGET}, row, tmp_path
        )
        assert pair["profile_kind"] == ("native" if native_available else "gecko")
        assert pair["before"]["task_id"] == (
            f"native-{BEFORE}" if native_available else "A" * 22
        )
        assert pair["after"]["task_id"] == (
            f"native-{TARGET}" if native_available else "B" * 22
        )
        if native_available:
            assert all(
                c.args[0].startswith("native")
                for c in client.download_profile.await_args_list
            )
        else:
            assert pair["fallback_reason"]


@pytest.mark.parametrize("variant", ["no-nv", "chrome"])
async def test_native_profiles_preserve_runtime_and_application_variants(
    tmp_path, row, profile_task, variant
):
    from unittest.mock import AsyncMock

    reference = profile_task(row)
    measurement = (
        reference + "-no-nv"
        if variant == "no-nv"
        else reference.replace("-firefox-", "-chrome-")
    )
    if variant == "no-nv":
        row["series_signature"]["extra_options"].append("no-nv")
    else:
        row["series_signature"]["application"] = "chrome"
    jobs = [
        {
            "id": ident,
            "push_id": push,
            "platform": row["series_signature"]["machine_platform"],
            "option_collection_hash": "opt",
            "state": "completed",
            "result": "success",
            "job_type_name": label.replace(
                "speedometer3", "speedometer3-native-profiling"
            ),
            "task_id": task,
        }
        for ident, push, label, task in (
            (1, BEFORE, measurement, "correct-before"),
            (2, BEFORE, reference, "wrong-before"),
            (3, TARGET, reference, "wrong-after"),
            (4, TARGET, measurement, "correct-after"),
        )
    ]
    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.tasks = AsyncMock(return_value=[measurement])
        client.get = treeherder(jobs)
        client.download_profile = AsyncMock(
            side_effect=lambda task, retry, dest: {"task_id": task}
        )
        pair = await client.profile_pair(
            {"repository": "autoland", "push_id": TARGET}, row, tmp_path
        )
    assert pair["before"]["task_id"] == "correct-before"
    assert pair["after"]["task_id"] == "correct-after"
    assert pair["measurement_task"] == measurement
    assert pair["application"] == row["series_signature"].get("application", "firefox")
    assert pair["extra_options"] == sorted(row["series_signature"]["extra_options"])


@pytest.mark.parametrize("invalid", ["revision", "repository", "configuration"])
async def test_fallback_profiles_require_verifiable_source_tasks(
    tmp_path, row, profile_task, invalid
):
    from unittest.mock import AsyncMock

    row["series_signature"]["suite"] = "facebook"
    row["prev_taskcluster_metadata"] = {"task_id": "A" * 22}
    row["taskcluster_metadata"] = {"task_id": "B" * 22}
    task = {
        "metadata": {"name": profile_task(row)},
        "payload": {
            "env": {
                "GECKO_HEAD_REV": "a" * 40,
                "GECKO_HEAD_REPOSITORY": "https://hg.mozilla.org/integration/autoland",
            }
        },
    }
    if invalid == "revision":
        task["payload"]["env"].pop("GECKO_HEAD_REV")
    elif invalid == "repository":
        task["payload"]["env"]["GECKO_HEAD_REPOSITORY"] = "https://hg.mozilla.org/try"
    else:
        task["metadata"]["name"] += "-no-nv"
    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = treeherder(task=task)
        client.download_profile = AsyncMock()
        with pytest.raises(EvidenceError, match="No native or Gecko profile pair"):
            await client.profile_pair(
                {"repository": "autoland", "push_id": TARGET}, row, tmp_path
            )
        client.download_profile.assert_not_awaited()


def _history(signature_id, pushes):
    """{(push_id, offset_minutes): job_count} -> a performance/data response."""
    points = []
    job = 0
    for (push, minutes), count in pushes.items():
        for _ in range(count):
            job += 1
            points.append(
                {
                    "signature_id": signature_id,
                    "push_id": push,
                    "push_timestamp": 1_800_000_000 + minutes * 60,
                    "job_id": job,
                    "value": 100.0,
                }
            )
    return {"hash": points}


def test_baselines_widen_to_neighbouring_pushes(row):
    from hackbot_agents.perf_regression.evidence import baselines

    alert = {"push_id": 100, "push_timestamp": 1_800_000_000}
    history = _history(
        row["series_signature"]["id"],
        # One job per push before the alert, one at the alert push.
        {
            (90, -50): 1,
            (94, -30): 1,
            (96, -20): 2,
            (99, -10): 1,
            (100, 0): 1,
            (103, 10): 1,
            (105, 20): 3,
        },
    )
    good, bad, pushes = baselines(alert, [row], history)
    key = metric_key(row["series_signature"])
    # Good: newest earlier pushes until 5 jobs; bad: the alert push, then the
    # next push only because the alert push alone has fewer than 2 jobs.
    assert pushes[key] == {
        "good_push_ids": [99, 96, 94, 90],
        "bad_push_ids": [100, 103],
    }
    assert len(good[key]) == 5 and len(bad[key]) == 2


def test_baselines_never_start_after_the_culprit_push(row):
    from hackbot_agents.perf_regression.evidence import baselines

    alert = {"push_id": 100, "push_timestamp": 1_800_000_000}
    history = _history(row["series_signature"]["id"], {(99, -10): 3, (103, 10): 4})
    _, bad, pushes = baselines(alert, [row], history)
    key = metric_key(row["series_signature"])
    assert bad[key] == [] and pushes[key]["bad_push_ids"] == []


def test_reassigned_sparse_rows_start_by_their_own_alert_push(row):
    from hackbot_agents.perf_regression.evidence import baselines

    alert = {"push_id": 100, "push_timestamp": 1_800_000_000}
    history = _history(
        row["series_signature"]["id"], {(99, -10): 3, (103, 10): 4, (106, 20): 4}
    )
    key = metric_key(row["series_signature"])
    _, bad, pushes = baselines(alert, [row], history, {row["id"]: (1_800_000_600, 103)})
    assert pushes[key]["bad_push_ids"] == [103] and len(bad[key]) == 4
    _, bad, _ = baselines(alert, [row], history, {row["id"]: (1_800_000_300, 101)})
    assert bad[key] == []


async def test_anchors_use_the_reassigned_rows_own_summary(row):
    from unittest.mock import AsyncMock

    row["status"] = 2
    row["summary_id"] = 54448
    other = {**row, "id": row["id"] + 1, "summary_id": 54000}
    summaries = {
        54448: {"repository": "autoland", "push_id": 103, "push_timestamp": 10},
        54000: {"repository": "mozilla-central", "push_id": 7, "push_timestamp": 5},
    }

    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = AsyncMock(
            side_effect=lambda url: summaries[int(url.rstrip("/").rsplit("/", 1)[-1])]
        )
        alert = {"id": 54516, "repository": "autoland"}
        assert await client.anchors(alert, [row, other]) == {row["id"]: (10, 103)}


async def test_selection_skips_metrics_that_cannot_be_validated(row):
    from copy import deepcopy
    from unittest.mock import AsyncMock

    rows = []
    for index, percent in enumerate([50, 40, 30, 20, 10]):
        metric = deepcopy(row)
        metric["id"] = 265000 + index
        metric["amount_pct"] = percent
        metric["series_signature"] = {
            **row["series_signature"],
            "id": 5000 + index,
            "test": str(index),
        }
        rows.append(metric)
    alert = {"id": 1, "push_id": 100, "push_timestamp": 1_800_000_000, "alerts": rows}
    history = {}
    for index in range(5):
        # Metric 1 has no data at the culprit push; the rest have enough.
        jobs = {(99, -10): 3} if index == 1 else {(99, -10): 3, (100, 0): 3}
        history[str(index)] = _history(5000 + index, jobs)["hash"]
    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        # Metric 2 has no CI task; metrics 0, 3 and 4 remain.
        client.row_tasks = AsyncMock(
            return_value={265000: "test-a", 265003: "test-b", 265004: "test-b"}
        )
        selected, tasks, excluded = await client.select(alert, history)
    assert [r["id"] for r in selected] == [265000, 265003, 265004]
    assert tasks == ["test-a", "test-b"]
    assert [(e["row_id"], e["reason"].split(" ")[0]) for e in excluded] == [
        (265001, "Not"),
        (265002, "No"),
    ]


@pytest.mark.parametrize(
    "label,platform,expected",
    [
        (
            "test-linux2404-64-shippable/opt-browsertime-speedometer3",
            "linux2404-64-shippable",
            True,
        ),
        (
            "perftest-linux-tr8ns-perf-tiny-linux2404-64-shippable/opt",
            "linux2404-64-shippable",
            True,
        ),
        (
            "perftest-linux-tr8ns-perf-tiny-linux2404-64-shippable/opt",
            "windows11-64-shippable",
            False,
        ),
        (
            "test-windows11-64-shippable/opt-speedometer3",
            "linux2404-64-shippable",
            False,
        ),
    ],
)
def test_task_labels_match_their_platform(label, platform, expected):
    from hackbot_agents.perf_regression.evidence import label_matches_platform

    assert label_matches_platform(label, platform) is expected


async def test_reselection_requires_rows_to_remain_eligible(row):
    from unittest.mock import AsyncMock

    alert = {"id": 1, "alerts": [row]}
    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.tasks = AsyncMock(return_value=["test-a"])
        assert await client.selected(alert, [row["id"]]) == ([row], ["test-a"])
        row["status"] = 3  # Marked invalid by a sheriff since.
        with pytest.raises(EvidenceError, match="no longer eligible"):
            await client.selected(alert, [row["id"]])


async def test_unlinked_alert_is_retryable_but_mismatch_is_not():
    from unittest.mock import AsyncMock

    from hackbot_agents.perf_regression.evidence import EvidencePending

    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = AsyncMock(return_value={"bug_number": None})
        with pytest.raises(EvidencePending):
            await client.alert(5, 1)
        client.get = AsyncMock(return_value={"bug_number": 2})
        with pytest.raises(EvidenceError) as caught:
            await client.alert(5, 1)
        assert not isinstance(caught.value, EvidencePending)


def test_points_without_job_records_are_ignored(row):
    from hackbot_agents.perf_regression.evidence import baselines

    alert = {"push_id": 100, "push_timestamp": 1_800_000_000}
    history = _history(row["series_signature"]["id"], {(99, -10): 3, (100, 0): 3})
    history["hash"] += [
        {**history["hash"][0], "job_id": None},
        {**history["hash"][-1], "job_id": None},
    ]
    good, bad, _ = baselines(alert, [row], history)
    key = metric_key(row["series_signature"])
    assert len(good[key]) == 3 and len(bad[key]) == 3


async def test_build_tasks_are_never_selected(row):
    from unittest.mock import AsyncMock

    row["taskcluster_metadata"] = {"task_id": "build"}
    async with httpx.AsyncClient() as http:
        client = EvidenceClient(http)
        client.get = AsyncMock(
            side_effect=[
                {"tasks": ["build-linux64-shippable/opt"]},
                {"metadata": {"name": "build-linux64-shippable/opt"}},
            ]
        )
        assert await client.row_tasks({"id": 1}, [row], history={}) == {}


def test_suite_limit_keeps_only_those_rows_and_reports_the_rest(row):
    from copy import deepcopy

    from hackbot_agents.perf_regression.evidence import (
        OutOfScope,
        regression_candidates,
    )

    other = deepcopy(row)
    other["id"] = 265001
    other["amount_pct"] = 50
    other["series_signature"]["suite"] = "tp6"
    alert = {"alerts": [other, row]}
    # The larger tp6 regression is skipped; Speedometer 3 is still found.
    assert regression_candidates(alert, ["Speedometer3"]) == [row]
    assert len(regression_candidates(alert)) == 2
    with pytest.raises(OutOfScope, match="regressed tp6"):
        regression_candidates({"alerts": [other]}, ["speedometer3"])


async def test_out_of_scope_alert_downloads_nothing_else(tmp_path, row):
    from unittest.mock import AsyncMock

    from hackbot_agents.perf_regression.evidence import OutOfScope

    row["series_signature"]["suite"] = "damp"
    async with httpx.AsyncClient() as http:
        collector = EvidenceClient(http)
        collector.bug = AsyncMock(return_value={"bug": {}, "comments": []})
        collector.alert = AsyncMock(return_value={"alerts": [row]})
        collector.history = AsyncMock()
        collector.git_revision = AsyncMock()
        with pytest.raises(OutOfScope):
            await collector.collect(1, 2, tmp_path, ["speedometer3"])
        collector.history.assert_not_awaited()
        collector.git_revision.assert_not_awaited()
    assert (tmp_path / "alert.json").exists()
