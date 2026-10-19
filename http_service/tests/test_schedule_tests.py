# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from typing import Callable

import hglib
import orjson
import pytest
import responses
import zstandard
from responses import matchers

from bugbug import test_scheduling
from bugbug.models import testselect
from bugbug_http import models


@pytest.mark.parametrize(
    "labels_to_choose, groups_to_choose, reduced_labels, config_groups",
    [
        # one from label, one from group
        (
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": 0.9},
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": ["test-linux1804-64/opt"]},
        ),
        # one from label, none from group
        (
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": 0.9},
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": ["test-linux1804-64/opt"]},
        ),
        # none from label, one from group
        (
            {},
            {"test-group1": 0.9},
            {},
            {"test-group1": ["test-linux1804-64/opt", "test-windows10/debug"]},
        ),
        # two from label, one from group
        (
            {"test-linux1804-64-opt-label1": 0.9, "test-linux1804-64-opt-label2": 0.5},
            {"test-group2": 0.9},
            {"test-linux1804-64-opt-label1": 0.9},
            {"test-group2": ["test-linux1804-64/opt"]},
        ),
        # two redundant from label, one from group
        (
            {"test-linux1804-64/opt": 0.9, "test-windows10/opt": 0.8},
            {"test-group1": 0.9},
            {"test-linux1804-64/opt": 0.9},
            {"test-group1": ["test-linux1804-64/opt", "test-windows10/debug"]},
        ),
    ],
)
def test_simple_schedule(
    labels_to_choose: dict[str, float],
    groups_to_choose: dict[str, float],
    reduced_labels: dict[str, float],
    config_groups: dict[str, list[str]],
    mock_hgmo: None,
    mock_repo: tuple[str, str],
    mock_component_taskcluster_artifact: None,
    mock_coverage_mapping_artifact: None,
    mock_schedule_tests_classify: Callable[[dict[str, float], dict[str, float]], None],
) -> None:
    # The repo should be almost empty at first
    repo_dir, remote_repo_dir = mock_repo
    with hglib.open(str(repo_dir)) as hg:
        logs = hg.log()
        assert len(logs) == 4
        assert [log.desc.decode("utf-8") for log in logs] == [
            "Base history 3",
            "Base history 2",
            "Base history 1",
            "Base history 0",
        ]
    with hglib.open(str(remote_repo_dir)) as hg:
        rev = hg.log()[0].node.decode("ascii")[:12]

    mock_schedule_tests_classify(labels_to_choose, groups_to_choose)

    # Scheduling a test on a revision should apply changes in the repo
    assert models.schedule_tests("mozilla-central", rev) == "OK"

    # Check changes have been applied
    with hglib.open(str(repo_dir)) as hg:
        assert len(hg.log()) == 5
        assert [log.desc.decode("utf-8") for log in hg.log()] == [
            "Pulled from remote",
            "Base history 3",
            "Base history 2",
            "Base history 1",
            "Base history 0",
        ]

    # Assert the test selection result is stored in Redis.
    value = models.redis.get(f"bugbug:job_result:schedule_tests:mozilla-central_{rev}")
    assert value is not None
    result = orjson.loads(zstandard.ZstdDecompressor().decompress(value))
    assert len(result) == 7
    assert result["tasks"] == labels_to_choose
    assert result["groups"] == groups_to_choose
    assert result["reduced_tasks"] == reduced_labels
    assert result["reduced_tasks_higher"] == reduced_labels
    assert result["known_tasks"] == ["prova"]
    # The mock models don't have confidence thresholds.
    assert result["confidence_thresholds"] == {}
    assert {k: set(v) for k, v in result["config_groups"].items()} == {
        k: set(v) for k, v in config_groups.items()
    }


def test_schedule_with_confidence_thresholds(
    monkeypatch: pytest.MonkeyPatch,
    mock_hgmo: None,
    mock_repo: tuple[str, str],
    mock_component_taskcluster_artifact: None,
    mock_coverage_mapping_artifact: None,
    mock_schedule_tests_classify: Callable[[dict[str, float], dict[str, float]], None],
) -> None:
    _, remote_repo_dir = mock_repo
    with hglib.open(str(remote_repo_dir)) as hg:
        rev = hg.log()[0].node.decode("ascii")[:12]

    mock_schedule_tests_classify(
        {"test-linux1804-64-opt-label1": 0.9, "test-linux1804-64-opt-label2": 0.35},
        {"test-group1": 0.35, "test-group2": 0.2},
    )

    thresholds = {
        "tasks": {"low": 0.3, "medium": 0.4, "high": 0.95},
        "groups": {"low": 0.3, "medium": 0.5, "high": 0.7},
    }

    class ModelCache:
        def get(self, model_name):
            if "group" in model_name:
                model = testselect.TestGroupSelectModel()
                model.confidence_thresholds = thresholds["groups"]
            else:
                model = testselect.TestLabelSelectModel()
                model.confidence_thresholds = thresholds["tasks"]
            return model

    monkeypatch.setattr(models, "MODEL_CACHE", ModelCache())

    assert models.schedule_tests("mozilla-central", rev) == "OK"

    value = models.redis.get(f"bugbug:job_result:schedule_tests:mozilla-central_{rev}")
    assert value is not None
    result = orjson.loads(zstandard.ZstdDecompressor().decompress(value))
    assert result["confidence_thresholds"] == thresholds
    # Runnables are returned down to the lowest threshold, rather than 0.5.
    assert result["tasks"] == {
        "test-linux1804-64-opt-label1": 0.9,
        "test-linux1804-64-opt-label2": 0.35,
    }
    assert result["groups"] == {"test-group1": 0.35}
    # The reduced tasks use the medium and high thresholds.
    assert result["reduced_tasks"] == {"test-linux1804-64-opt-label1": 0.9}
    assert result["reduced_tasks_higher"] == {}


JOB_PROPERTY_NAMES = [
    "failure_classification_id",
    "job_type_name",
    "push_id",
    "task_id",
]


def mock_treeherder_recent_failures() -> None:
    responses.add(
        responses.GET,
        "https://treeherder.mozilla.org/api/project/autoland/push/",
        match=[matchers.query_param_matcher({"count": "20", "tochange": "rev"})],
        json={
            "results": [
                {"id": 2, "revision": "rev2"},
                {"id": 1, "revision": "rev1"},
            ]
        },
    )
    responses.add(
        responses.GET,
        "https://treeherder.mozilla.org/api/jobs/",
        match=[
            matchers.query_param_matcher(
                {"push_id__in": "2,1", "result": "testfailed", "count": "2000"}
            )
        ],
        json={
            "job_property_names": JOB_PROPERTY_NAMES,
            "results": [
                # Not classified.
                [1, "test-linux1804-64-opt-label1", 1, "task1"],
                # Classified as intermittent.
                [4, "test-linux1804-64-opt-label2", 1, "task2"],
                # Classified as infra.
                [5, "test-linux1804-64/opt", 1, "task5"],
                # Classified as expected fail.
                [3, "test-linux1804-64/opt", 1, "task6"],
                # Not classified, without group results.
                [1, "test-linux1804-64/opt-mochitest-1", 1, "task7"],
                # Not classified, without group results nor test paths.
                [1, "build-linux64/opt", 1, "task8"],
            ],
            "next": "https://treeherder.mozilla.org/api/jobs/?page=2",
        },
    )
    responses.add(
        responses.GET,
        "https://treeherder.mozilla.org/api/jobs/",
        match=[matchers.query_param_matcher({"page": "2"})],
        json={
            "job_property_names": JOB_PROPERTY_NAMES,
            "results": [
                # New failure not classified, on a task unknown to the model.
                [6, "test-unknown", 2, "task3"],
                # Classified as autoclassified intermittent.
                [7, "test-windows10/opt", 2, "task4"],
            ],
            "next": None,
        },
    )
    responses.add(
        responses.GET,
        "https://treeherder.mozilla.org/api/project/autoland/push/group_results/",
        match=[matchers.query_param_matcher({"revision": "rev1"})],
        json={
            "task1": {
                "test-group1:test-group-parent": False,
                "test-group2": True,
                "/wpt-dir": False,
                ":/_mozilla/wpt-dir": False,
            },
            "task2": {"test-group2": False},
            "task5": {"test-group2": False},
            "task6": {"test-group2": False},
        },
    )
    responses.add(
        responses.GET,
        "https://treeherder.mozilla.org/api/project/autoland/push/group_results/",
        match=[matchers.query_param_matcher({"revision": "rev2"})],
        json={
            "task3": {"test-unknown-group": False},
            "task4": {"test-group2": False},
        },
    )
    responses.add(
        responses.GET,
        "https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/task7/artifacts",
        json={
            "artifacts": [
                {"name": "public/logs/live_backing.log"},
                {"name": "public/test_info/mochitest-plain_errorsummary.log"},
            ]
        },
    )
    responses.add(
        responses.GET,
        "https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/task7/artifacts/public/test_info/mochitest-plain_errorsummary.log",
        body=b"\n".join(
            orjson.dumps(line)
            for line in [
                # Groups which did not fail are not selected.
                {"action": "test_groups", "groups": ["test-group2", "test-group3"]},
                {
                    "action": "test_result",
                    "test": "test1.html",
                    "group": "test-group2",
                    "status": "FAIL",
                    "expected": "PASS",
                },
            ]
        ),
    )


@pytest.mark.parametrize(
    "group, expected",
    [
        ("test-group1", "test-group1"),
        ("test-group1:test-group-parent", "test-group1"),
        ("/fetch/api/request", "testing/web-platform/tests/fetch/api/request"),
        (":/fetch/api/request", "testing/web-platform/tests/fetch/api/request"),
        (
            "/_mozilla/fetch/api/request",
            "testing/web-platform/mozilla/tests/fetch/api/request",
        ),
    ],
)
def test_normalize_group(group: str, expected: str) -> None:
    assert models._normalize_group(group) == expected


def test_get_recent_failures(
    mock_schedule_tests_classify: Callable[[dict[str, float], dict[str, float]], None],
) -> None:
    past_failures_data = test_scheduling.PastFailures("group", False)
    past_failures_data.all_runnables = [
        *past_failures_data.all_runnables,
        "test-group3",
        "testing/web-platform/tests/wpt-dir",
        "testing/web-platform/mozilla/tests/wpt-dir",
    ]
    past_failures_data.close()

    mock_treeherder_recent_failures()

    tasks, groups = models._get_recent_failures("autoland", "rev")

    assert tasks == {"test-linux1804-64-opt-label1"}
    assert groups == {
        "test-group1",
        "test-group2",
        "testing/web-platform/tests/wpt-dir",
        "testing/web-platform/mozilla/tests/wpt-dir",
    }


def add_backout(remote_repo_dir: str) -> bytes:
    """Back out the last commit of the remote repository, returning the backout node."""
    with hglib.open(str(remote_repo_dir)) as hg:
        backedout = hg.log(limit=1)[0].node.decode("ascii")
        hg.backout(
            rev=backedout,
            message=f"Backed out changeset {backedout[:12]} (bug 1) for causing failures",
            user="sheriff",
        )
        return hg.log(limit=1)[0].node


def test_schedule_backout(
    monkeypatch: pytest.MonkeyPatch,
    mock_hgmo: None,
    mock_repo: tuple[str, str],
    mock_component_taskcluster_artifact: None,
    mock_coverage_mapping_artifact: None,
    mock_schedule_tests_classify: Callable[[dict[str, float], dict[str, float]], None],
) -> None:
    _, remote_repo_dir = mock_repo
    node = add_backout(remote_repo_dir)
    rev = node.decode("ascii")[:12]
    monkeypatch.setattr(models, "get_hgmo_stack", lambda branch, r: [node])

    # The mock models would select these, but they are ignored for backouts.
    mock_schedule_tests_classify(
        {"test-linux1804-64-opt-label2": 0.9}, {"test-group2": 0.9}
    )

    def _get_recent_failures(project, r):
        assert (project, r) == ("autoland", node.decode("ascii"))
        return {"test-linux1804-64/opt", "test-windows10/opt"}, {"test-group1"}

    monkeypatch.setattr(models, "_get_recent_failures", _get_recent_failures)

    assert models.schedule_tests("integration/autoland", rev) == "OK"

    value = models.redis.get(
        f"bugbug:job_result:schedule_tests:integration/autoland_{rev}"
    )
    assert value is not None
    result = orjson.loads(zstandard.ZstdDecompressor().decompress(value))
    assert result["tasks"] == {
        "test-linux1804-64/opt": 1.0,
        "test-windows10/opt": 1.0,
    }
    assert result["groups"] == {"test-group1": 1.0}
    # The recently failing tasks are not reduced, even if they are redundant.
    assert result["reduced_tasks"] == result["tasks"]
    assert result["reduced_tasks_higher"] == result["tasks"]
    assert {k: set(v) for k, v in result["config_groups"].items()} == {
        "test-group1": {
            "test-linux1804-64/opt",
            "test-windows10/debug",
            "test-windows10/opt",
        }
    }


def test_schedule_backout_try(
    monkeypatch: pytest.MonkeyPatch,
    mock_hgmo: None,
    mock_repo: tuple[str, str],
    mock_component_taskcluster_artifact: None,
    mock_coverage_mapping_artifact: None,
    mock_schedule_tests_classify: Callable[[dict[str, float], dict[str, float]], None],
) -> None:
    _, remote_repo_dir = mock_repo
    node = add_backout(remote_repo_dir)
    rev = node.decode("ascii")[:12]
    monkeypatch.setattr(models, "get_hgmo_stack", lambda branch, r: [node])

    # The mock models would select these, but they are ignored for backouts.
    mock_schedule_tests_classify(
        {"test-linux1804-64-opt-label2": 0.9}, {"test-group2": 0.9}
    )

    def _get_recent_failures(project, r):
        raise AssertionError("Recent failures should not be used on try")

    monkeypatch.setattr(models, "_get_recent_failures", _get_recent_failures)

    assert models.schedule_tests("try", rev) == "OK"

    value = models.redis.get(f"bugbug:job_result:schedule_tests:try_{rev}")
    assert value is not None
    result = orjson.loads(zstandard.ZstdDecompressor().decompress(value))
    assert result["tasks"] == {}
    assert result["groups"] == {}


def test_schedule_backout_treeherder_failure(
    monkeypatch: pytest.MonkeyPatch,
    mock_hgmo: None,
    mock_repo: tuple[str, str],
    mock_component_taskcluster_artifact: None,
    mock_coverage_mapping_artifact: None,
    mock_schedule_tests_classify: Callable[[dict[str, float], dict[str, float]], None],
) -> None:
    _, remote_repo_dir = mock_repo
    node = add_backout(remote_repo_dir)
    rev = node.decode("ascii")[:12]
    monkeypatch.setattr(models, "get_hgmo_stack", lambda branch, r: [node])

    mock_schedule_tests_classify(
        {"test-linux1804-64-opt-label2": 0.9}, {"test-group2": 0.9}
    )

    # Simulate an unexpected response from Treeherder.
    def _get_recent_failures(project, r):
        raise KeyError("results")

    monkeypatch.setattr(models, "_get_recent_failures", _get_recent_failures)

    assert models.schedule_tests("integration/autoland", rev) == "OK"

    value = models.redis.get(
        f"bugbug:job_result:schedule_tests:integration/autoland_{rev}"
    )
    assert value is not None
    result = orjson.loads(zstandard.ZstdDecompressor().decompress(value))
    assert result["tasks"] == {}
    assert result["groups"] == {}
