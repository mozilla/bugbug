import json

import httpx
import pytest
from app import taskcluster

DECISION = "JAfynVmyQUSOJ1xXIdCuFg"


def _task(parent=DECISION, group=DECISION):
    extra = {"parent": parent} if parent is not None else {}
    return {"taskGroupId": group, "extra": extra}


def test_decision_scheduled_task_is_not_action_scheduled():
    # Everything the push itself schedules is parented to the decision task,
    # which is also the task group.
    assert taskcluster.is_action_scheduled(_task()) is False


def test_backfilled_task_is_action_scheduled():
    # Real shape from bug 6395: the failing build points at the backfill
    # action-callback task while its group is still the original decision task.
    task = _task(parent="Fg-IZvVBTwGr22Fx81om4A")
    assert taskcluster.is_action_scheduled(task) is True


def test_missing_parent_is_not_action_scheduled():
    assert taskcluster.is_action_scheduled(_task(parent=None)) is False
    assert taskcluster.is_action_scheduled({"taskGroupId": DECISION}) is False
    assert taskcluster.is_action_scheduled({}) is False


def test_get_hg_revision_reads_gecko_head_rev():
    task = {"payload": {"env": {"GECKO_HEAD_REV": "abc123"}}}
    assert taskcluster.get_hg_revision(task) == "abc123"
    assert taskcluster.get_hg_revision({"payload": {"env": {}}}) is None
    assert taskcluster.get_hg_revision({}) is None


def test_get_hg_revision_maps_github_checkout_to_hg(monkeypatch):
    monkeypatch.setattr(taskcluster.lando, "git_to_hg", {"cf70795": "39b0141"}.get)
    task = {
        "payload": {
            "env": {
                "GECKO_HEAD_REV": "cf70795",
                "GECKO_HEAD_REPOSITORY": "https://github.com/mozilla-firefox/firefox",
            }
        }
    }
    assert taskcluster.get_hg_revision(task) == "39b0141"


class _FakeQueue:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.listed = []

    def listLatestArtifacts(self, task_id):
        self.listed.append(task_id)
        return {"artifacts": [{"name": name} for name in self.artifacts]}

    def buildUrl(self, method, task_id, name):
        return f"https://tc/{task_id}/{name}"


@pytest.fixture
def errorsummary(monkeypatch):
    """Serve {artifact name: [errorsummary lines]} for every task."""

    def setup(artifacts):
        queue = _FakeQueue(artifacts)
        monkeypatch.setattr(taskcluster, "_get_queue", lambda: queue)

        def fake_get(url, **kwargs):
            name = url.split("/", 4)[4]
            body = "\n".join(json.dumps(line) for line in artifacts[name])
            return httpx.Response(200, text=body, request=httpx.Request("GET", url))

        monkeypatch.setattr(taskcluster.httpx, "get", fake_get)
        return queue

    taskcluster._group_results_cache.clear()
    yield setup
    taskcluster._group_results_cache.clear()


def _group_result(group, status):
    return {"action": "group_result", "group": group, "status": status, "duration": 1}


def test_group_results_read_the_errorsummary_logs(errorsummary):
    errorsummary(
        {
            "public/logs/live_backing.log": [],
            "public/test_info/mochitest-plain_errorsummary.log": [
                {"action": "test_groups", "groups": ["a.ini", "b.ini", "c.ini"]},
                # A crash of a test which is retried is still written, but its group
                # passes when the retry does.
                {
                    "action": "crash",
                    "test": "a.html",
                    "group": "a.ini",
                    "signature": "x",
                },
                _group_result("a.ini", "OK"),
                _group_result("b.ini", "ERROR"),
                _group_result("c.ini", "SKIP"),
            ],
            "public/test_info/mochitest-plain-2_errorsummary.log": [
                _group_result("a.ini", "ERROR"),
                _group_result("d.ini", "OK"),
            ],
        }
    )
    # A group fails if any harness invocation failed it.
    assert taskcluster.group_results("T1") == {
        "a.ini": False,
        "b.ini": False,
        "d.ini": True,
    }


def test_group_results_are_fetched_once_per_task(errorsummary):
    queue = errorsummary({"x_errorsummary.log": [_group_result("a.ini", "OK")]})
    taskcluster.group_results("T1")
    taskcluster.group_results("T1")
    taskcluster.group_results("T2")
    assert queue.listed == ["T1", "T2"]


def test_failing_groups_returns_only_failures(errorsummary):
    errorsummary(
        {
            "x_errorsummary.log": [
                _group_result("a.ini", "ERROR"),
                _group_result("b.ini", "OK"),
                _group_result("/", "ERROR"),
            ]
        }
    )
    assert taskcluster.failing_groups("T1") == ["a.ini"]


def test_task_with_no_failing_group_reports_nothing(errorsummary):
    errorsummary({"x_errorsummary.log": [_group_result("a.ini", "OK")]})
    assert taskcluster.failing_groups("T1") == []


def test_task_without_group_results_raises_rather_than_reporting_nothing(
    errorsummary,
):
    # A task-level failure (crash, timeout) ends before the harness writes the group
    # results. That does not mean "nothing failed".
    errorsummary(
        {
            "x_errorsummary.log": [
                {"action": "test_groups", "groups": ["a.ini"]},
                {
                    "action": "test_result",
                    "test": "a.html",
                    "group": "a.ini",
                    "status": "FAIL",
                    "expected": "PASS",
                },
            ]
        }
    )
    with pytest.raises(taskcluster.GroupResultsUnavailable):
        taskcluster.failing_groups("T1")
