# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import contextlib
import json
import os
import re
from pathlib import Path

# mozci.data needs to be imported before mozci.task to avoid a circular import.
import mozci.data  # noqa: F401
import orjson
import pytest
import requests
import responses
import tenacity
from mozci import task as mozci_task

from bugbug_http import boot, refresh


def test_retrieve_build_platforms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        boot.build_platforms,
        "retrieve_built_files",
        lambda: {"macosx": ["widget/cocoa/nsCocoaWindow.mm"]},
    )

    boot.retrieve_build_platforms()

    with open("build_platforms.json") as f:
        assert json.load(f) == {"macosx": ["widget/cocoa/nsCocoaWindow.mm"]}
    # The file is written atomically, through a temporary file.
    assert not any(name.endswith(".tmp") for name in os.listdir())


def test_refresh_data_keeps_going_after_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fail() -> None:
        calls.append("tasks")
        raise Exception("decision task unavailable")

    monkeypatch.setattr(boot, "retrieve_schedulable_tasks", fail)
    monkeypatch.setattr(
        boot, "retrieve_build_platforms", lambda: calls.append("build platforms")
    )
    monkeypatch.setattr(boot, "refresh_commits", lambda: calls.append("commits"))

    boot.refresh_data()

    assert calls == ["tasks", "build platforms", "commits"]


def test_refresh_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(
        boot.repository,
        "pull",
        lambda repo_dir, branch, revision: calls.append(("pull", branch, revision)),
    )
    monkeypatch.setattr(boot, "update_commits", lambda: calls.append("update"))

    boot.refresh_commits()

    # The latest autoland commits are pulled before being mined.
    assert calls == [("pull", "integration/autoland", "tip"), "update"]


def test_refresh_main(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    now = [0.0]

    class Stop(Exception):
        pass

    def sleep(seconds: float) -> None:
        assert seconds == boot.CONFIG_SELECTION_TASKS_REFRESH_INTERVAL
        if len(calls) == 7:
            raise Stop()
        now[0] += seconds

    def refresh_config_selection_tasks() -> None:
        calls.append("tasks")
        if len(calls) == 1:
            raise Exception("pushlog unavailable")

    monkeypatch.setattr(boot, "REFRESH_INTERVAL", 30)
    monkeypatch.setattr(boot, "CONFIG_SELECTION_TASKS_REFRESH_INTERVAL", 10)
    monkeypatch.setattr(refresh.os, "nice", lambda increment: None)
    monkeypatch.setattr(refresh.time, "sleep", sleep)
    monkeypatch.setattr(refresh.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(boot, "refresh_data", lambda: calls.append("refresh"))
    monkeypatch.setattr(
        boot, "refresh_config_selection_tasks", refresh_config_selection_tasks
    )

    # The config selection tasks are refreshed often (also after a failure), the rest of the data
    # (including them) once per REFRESH_INTERVAL.
    with pytest.raises(Stop):
        refresh.main()
    assert calls == ["tasks", "tasks", "refresh", "tasks", "tasks", "refresh", "tasks"]


def _task(
    label: str,
    platform: str,
    manifests: list[str],
    optimization: dict | None = None,
    runtime: dict | None = None,
) -> dict:
    return {
        "label": label,
        "attributes": {"test_manifests": manifests},
        "optimization": optimization or {"test": ["firefox", "mochitest"]},
        "task": {
            "extra": {
                "treeherder-platform": platform,
                "suite": "mochitest-plain",
                "test-setting": {"runtime": runtime or {}},
            }
        },
    }


TASK_GRAPH = {
    "a": _task(
        "test-linux2404-64/opt-mochitest-plain-1", "linux2404-64/opt", ["dom/a.toml"]
    ),
    "b": _task(
        "test-linux2404-64/opt-mochitest-plain-2", "linux2404-64/opt", ["dom/b.toml"]
    ),
    # Shippable tasks are too costly: their non-shippable equivalents are selected instead.
    "c": _task(
        "test-linux2404-64-shippable/opt-mochitest-plain-1",
        "linux2404-64-shippable/opt",
        ["dom/a.toml"],
    ),
    "d": _task(
        "test-windows11-64-25h2-asan/opt-mochitest-plain-1",
        "windows11-64-25h2/asan",
        ["dom/a.toml"],
    ),
    # The variants come from the test settings.
    "h": _task(
        "test-linux2404-64/opt-mochitest-plain-xorig-1",
        "linux2404-64/opt",
        ["dom/a.toml"],
        runtime={"xorigin": True},
    ),
    # Not optimized by test selection.
    "e": _task(
        "test-linux2404-64/opt-mochitest-plain-standalone-1",
        "linux2404-64/opt",
        ["dom/a.toml"],
        {"skip-unless-backstop": None},
    ),
    # Not a target task of the recent pushes.
    "f": _task(
        "test-linux2404-64/debug-mochitest-plain-1",
        "linux2404-64/debug",
        ["dom/a.toml"],
    ),
    # Not a test task running manifests.
    "g": {
        "label": "build-linux64/opt",
        "attributes": {},
        "optimization": None,
        "task": {},
    },
}

KNOWN_TASKS = {
    "test-linux2404-64/opt-mochitest-plain-1",
    "test-linux2404-64/opt-mochitest-plain-2",
    "test-linux2404-64-shippable/opt-mochitest-plain-3",
    "test-windows11-64-25h2-asan/opt-mochitest-plain-1",
    "test-linux2404-64/opt-mochitest-plain-xorig-1",
    "test-linux2404-64/opt-mochitest-plain-standalone-1",
    "build-linux64/opt",
}

CONFIG_SELECTION_TASKS = {
    # The manifests of all the chunks.
    "test-linux2404-64/opt-mochitest-plain": {
        "config": "test-linux2404-64/opt-*",
        "manifests": ["dom/a.toml", "dom/b.toml"],
    },
    "test-windows11-64-25h2-asan/opt-mochitest-plain": {
        "config": "test-windows11-64-25h2/asan-*",
        "manifests": ["dom/a.toml"],
    },
    "test-linux2404-64/opt-mochitest-plain-xorig": {
        "config": "test-linux2404-64/opt-*-xorig",
        "manifests": ["dom/a.toml"],
    },
}


def assert_requests_have_timeouts() -> None:
    """Stalled requests can't block the boot or the refresh (mozci's own requests aside)."""
    calls = [
        call
        for call in responses.calls
        if not call.request.url.startswith("https://hg.mozilla.org/mozilla-central/")
    ]
    assert calls
    for call in calls:
        assert call.request.req_kwargs["timeout"] == boot.REQUEST_TIMEOUT, (
            call.request.url
        )


AUTOLAND_VARIANTS = re.compile(
    r"https://hg.mozilla.org/integration/autoland/raw-file/[^/]+/taskcluster/test_configs/variants.yml"
)


@pytest.fixture(autouse=True)
def mock_test_variants() -> None:
    # mozci memoizes the test variants.
    mozci_task.fetch_test_variant_yaml.clear()
    # The suffixes of the test variants, of mozilla-central (retrieved by mozci) and of the pushes.
    responses.add(
        responses.GET,
        "https://hg.mozilla.org/mozilla-central/raw-file/default/taskcluster/test_configs/variants.yml",
        body="xorigin:\n  suffix: xorig\n",
    )
    responses.add(responses.GET, AUTOLAND_VARIANTS, body="xorigin:\n  suffix: xorig\n")


def test_get_config_selection_tasks() -> None:
    assert (
        boot.get_config_selection_tasks(TASK_GRAPH, KNOWN_TASKS)
        == CONFIG_SELECTION_TASKS
    )


def test_retrieve_schedulable_tasks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    # No tasks were retrieved before.
    with contextlib.suppress(FileNotFoundError):
        os.remove(boot.CONFIG_SELECTION_TASKS_PATH)
    # A transient failure is retried.
    responses.add(
        responses.GET,
        "https://hg.mozilla.org/integration/autoland/json-pushes",
        body=requests.ConnectionError("connection reset"),
    )
    responses.add(
        responses.GET,
        "https://hg.mozilla.org/integration/autoland/json-pushes",
        json={
            "pushes": {
                "1": {"changesets": ["rev1"]},
                "2": {"changesets": ["rev2"]},
                "3": {"changesets": ["rev3"]},
            }
        },
    )
    for rev, backstop in (("rev1", True), ("rev2", True), ("rev3", False)):
        responses.add(
            responses.GET,
            boot._decision_artifact_url(rev, "target-tasks.json"),
            json=sorted(KNOWN_TASKS),
        )
        responses.add(
            responses.GET,
            boot._decision_artifact_url(rev, "parameters.yml"),
            body=f"backstop: {'true' if backstop else 'false'}\n",
        )
    # The tasks come from the latest backstop push with some: the graph of rev2 has none.
    responses.add(
        responses.GET,
        boot._decision_artifact_url("rev2", "task-graph.json"),
        json={"g": TASK_GRAPH["g"]},
    )
    responses.add(
        responses.GET,
        boot._decision_artifact_url("rev1", "task-graph.json"),
        json=TASK_GRAPH,
    )
    boot.retrieve_schedulable_tasks.retry_with(wait=tenacity.wait_none())()
    assert_requests_have_timeouts()

    with open("known_tasks") as f:
        assert set(f.read().split("\n")) == KNOWN_TASKS
    with open("config_selection_tasks.json") as f:
        assert json.load(f) == {
            "backstop": "rev1",
            "tasks": CONFIG_SELECTION_TASKS,
            "new_tasks": {},
            # The test tasks of the backstop push which aren't eligible.
            "ignored": [
                "test-linux2404-64-shippable/opt-mochitest-plain",
                "test-linux2404-64/debug-mochitest-plain",
                "test-linux2404-64/opt-mochitest-plain-standalone",
            ],
            "processed": ["rev2", "rev3"],
            "not_backstops": ["rev3"],
        }


def test_get_full_graph_tasks() -> None:
    graph = {"a": TASK_GRAPH["a"], "c": TASK_GRAPH["c"], "g": TASK_GRAPH["g"]}
    graph = {task["label"]: task for task in graph.values()}
    labels = {"test-linux2404-64/opt-mochitest-plain-1", "build-linux64/opt"}
    expected = {label: graph[label] for label in labels}
    # The definitions are found in the text, as the full task graph is compact JSON.
    assert boot.get_full_graph_tasks(orjson.dumps(graph), labels) == expected
    # Otherwise, the whole graph is parsed.
    assert (
        boot.get_full_graph_tasks(json.dumps(graph, indent=2).encode(), labels)
        == expected
    )
    # Tasks which aren't in the graph are left out.
    assert (
        boot.get_full_graph_tasks(orjson.dumps(graph), {"test-unknown/opt-x-1"}) == {}
    )


def test_update_config_selection_tasks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    # No tasks were retrieved before.
    with contextlib.suppress(FileNotFoundError):
        os.remove(boot.CONFIG_SELECTION_TASKS_PATH)
    renamed = _task(
        "test-linux2404-64/opt-mochitest-plain-renamed-1",
        "linux2404-64/opt",
        ["dom/a.toml"],
    )
    renamed_shippable = _task(
        "test-linux2404-64-shippable/opt-mochitest-plain-renamed-1",
        "linux2404-64-shippable/opt",
        ["dom/a.toml"],
    )
    for rev, backstop in (
        ("b1", True),
        ("p1", False),
        ("p2", False),
        ("p3", False),
        ("b2", True),
    ):
        responses.add(
            responses.GET,
            boot._decision_artifact_url(rev, "parameters.yml"),
            body=f"backstop: {'true' if backstop else 'false'}\n",
        )
    responses.add(
        responses.GET,
        boot._decision_artifact_url("b1", "task-graph.json"),
        json=TASK_GRAPH,
    )
    # A target task of the backstop pushes isn't in their task graph: it's optimized otherwise
    # than by test selection.
    jittest = "test-linux2404-64/opt-jittest-1proc-1"
    responses.add(
        responses.GET,
        boot._decision_artifact_url("b1", "target-tasks.json"),
        json=sorted(KNOWN_TASKS) + [jittest],
    )
    responses.add(
        responses.GET,
        boot._decision_artifact_url("b2", "target-tasks.json"),
        json=sorted(KNOWN_TASKS) + [jittest, renamed["label"]],
    )
    # A push after the backstop push has tasks renamed since then (found among its target tasks,
    # as they could have been optimized away). Shippable ones are still ignored.
    responses.add(
        responses.GET,
        boot._decision_artifact_url("p1", "target-tasks.json"),
        json=sorted(KNOWN_TASKS)
        + [jittest, renamed["label"], renamed_shippable["label"]],
    )
    responses.add(
        responses.GET,
        boot._decision_artifact_url("p1", "full-task-graph.json"),
        body=orjson.dumps(
            {
                task["label"]: task
                for task in (*TASK_GRAPH.values(), renamed, renamed_shippable)
            }
        ),
    )
    # The target tasks of the next one aren't there yet, then they are (without unknown tasks, so
    # its full task graph isn't needed).
    responses.add(
        responses.GET,
        boot._decision_artifact_url("p2", "target-tasks.json"),
        status=404,
    )
    responses.add(
        responses.GET,
        boot._decision_artifact_url("p2", "target-tasks.json"),
        json=sorted(KNOWN_TASKS) + [renamed["label"], renamed_shippable["label"]],
    )

    # The push after it is processed meanwhile.
    responses.add(
        responses.GET,
        boot._decision_artifact_url("p3", "target-tasks.json"),
        json=sorted(KNOWN_TASKS),
    )

    def read() -> dict:
        with open("config_selection_tasks.json") as f:
            return json.load(f)

    ignored = [
        "test-linux2404-64-shippable/opt-mochitest-plain",
        "test-linux2404-64/debug-mochitest-plain",
        "test-linux2404-64/opt-jittest-1proc",
        "test-linux2404-64/opt-mochitest-plain-standalone",
    ]
    boot.update_config_selection_tasks(["b1", "p1", "p2", "p3"], KNOWN_TASKS)
    assert read() == {
        "backstop": "b1",
        "tasks": CONFIG_SELECTION_TASKS,
        "new_tasks": {
            "test-linux2404-64/opt-mochitest-plain-renamed": "test-linux2404-64/opt-*"
        },
        "ignored": sorted(
            ignored + ["test-linux2404-64-shippable/opt-mochitest-plain-renamed"]
        ),
        "processed": ["p1", "p3"],
        "not_backstops": ["p1", "p2", "p3"],
    }

    # p2 is processed once its target tasks are there.
    boot.update_config_selection_tasks(["b1", "p1", "p2", "p3"], KNOWN_TASKS)
    assert read()["processed"] == ["p1", "p2", "p3"]
    # The full task graph was only needed for p1's renamed tasks: the jittest task was already
    # known not to be eligible.
    full_graphs = [
        call.request.url
        for call in responses.calls
        if call.request.url.endswith("full-task-graph.json")
    ]
    assert full_graphs == [boot._decision_artifact_url("p1", "full-task-graph.json")]

    # A newer backstop push replaces the tasks, which include the renamed one, even if the target
    # tasks of the recent pushes are from before the rename (they are only refreshed daily). The
    # tasks found not to be eligible before are kept as long as recent pushes target them (the
    # shippable renamed one isn't among the tasks or the target tasks of the new backstop push).
    responses.add(
        responses.GET,
        boot._decision_artifact_url("b2", "task-graph.json"),
        json={**TASK_GRAPH, "r": renamed},
    )
    boot.update_config_selection_tasks(
        ["b1", "p1", "p2", "p3", "b2"], KNOWN_TASKS | {renamed_shippable["label"]}
    )
    assert read() == {
        "backstop": "b2",
        "tasks": {
            **CONFIG_SELECTION_TASKS,
            "test-linux2404-64/opt-mochitest-plain-renamed": {
                "config": "test-linux2404-64/opt-*",
                "manifests": ["dom/a.toml"],
            },
        },
        "new_tasks": {},
        "ignored": sorted(
            ignored + ["test-linux2404-64-shippable/opt-mochitest-plain-renamed"]
        ),
        "processed": [],
        "not_backstops": ["p1", "p2", "p3"],
    }
    # Pushes already known not to be backstop pushes aren't checked again.
    parameters_calls = [
        call.request.url
        for call in responses.calls
        if call.request.url.endswith("parameters.yml")
    ]
    assert len(parameters_calls) == len(set(parameters_calls))
    assert_requests_have_timeouts()


def test_update_config_selection_tasks_new_variant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    with contextlib.suppress(FileNotFoundError):
        os.remove(boot.CONFIG_SELECTION_TASKS_PATH)
    # A test variant added on autoland, which isn't on mozilla-central yet.
    new_variant = _task(
        "test-linux2404-64/opt-mochitest-plain-newv-1",
        "linux2404-64/opt",
        ["dom/a.toml"],
        runtime={"newvariant": True},
    )
    # A task of the same variant in a later push.
    renamed = _task(
        "test-linux2404-64/opt-mochitest-plain-renamed-newv-1",
        "linux2404-64/opt",
        ["dom/a.toml"],
        runtime={"newvariant": True},
    )
    graph = {**TASK_GRAPH, "n": new_variant}
    for rev, backstop in (("b1", True), ("p1", False)):
        responses.add(
            responses.GET,
            boot._decision_artifact_url(rev, "parameters.yml"),
            body=f"backstop: {'true' if backstop else 'false'}\n",
        )
    responses.add(
        responses.GET, boot._decision_artifact_url("b1", "task-graph.json"), json=graph
    )
    targets = sorted(KNOWN_TASKS) + [new_variant["label"]]
    responses.add(
        responses.GET,
        boot._decision_artifact_url("b1", "target-tasks.json"),
        json=targets,
    )
    responses.add(
        responses.GET,
        boot._decision_artifact_url("p1", "target-tasks.json"),
        json=targets + [renamed["label"]],
    )
    responses.add(
        responses.GET,
        boot._decision_artifact_url("p1", "full-task-graph.json"),
        body=orjson.dumps({**graph, renamed["label"]: renamed}),
    )
    responses.replace(
        responses.GET,
        AUTOLAND_VARIANTS,
        body="xorigin:\n  suffix: xorig\nnewvariant:\n  suffix: newv\n",
    )

    boot.update_config_selection_tasks(["b1", "p1"], KNOWN_TASKS)

    with open(boot.CONFIG_SELECTION_TASKS_PATH) as f:
        state = json.load(f)
    # The configs are named with the push's test variants, on the first update.
    assert state["tasks"]["test-linux2404-64/opt-mochitest-plain-newv"] == {
        "config": "test-linux2404-64/opt-*-newv",
        "manifests": ["dom/a.toml"],
    }
    assert state["new_tasks"] == {
        "test-linux2404-64/opt-mochitest-plain-renamed-newv": "test-linux2404-64/opt-*-newv"
    }
