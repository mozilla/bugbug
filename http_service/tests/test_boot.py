# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
from pathlib import Path

import pytest
import requests
import responses
import tenacity

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

    class Stop(Exception):
        pass

    def sleep(seconds: float) -> None:
        assert seconds == boot.REFRESH_INTERVAL
        if len(calls) == 2:
            raise Stop()

    monkeypatch.setattr(refresh.os, "nice", lambda increment: None)
    monkeypatch.setattr(refresh.time, "sleep", sleep)
    monkeypatch.setattr(boot, "refresh_data", lambda: calls.append("refresh"))

    # The data is refreshed again and again, after waiting.
    with pytest.raises(Stop):
        refresh.main()
    assert calls == ["refresh", "refresh"]


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


@pytest.fixture(autouse=True)
def mock_test_variants() -> None:
    # mozci retrieves the suffixes of the test variants.
    responses.add(
        responses.GET,
        "https://hg.mozilla.org/mozilla-central/raw-file/default/taskcluster/test_configs/variants.yml",
        body="xorigin:\n  suffix: xorig\n",
    )


def test_get_config_selection_tasks() -> None:
    assert (
        boot.get_config_selection_tasks(TASK_GRAPH, KNOWN_TASKS)
        == CONFIG_SELECTION_TASKS
    )


def test_retrieve_schedulable_tasks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    # A transient failure is retried.
    responses.add(
        responses.GET,
        "https://hg.mozilla.org/integration/autoland/json-pushes",
        body=requests.ConnectionError("connection reset"),
    )
    responses.add(
        responses.GET,
        "https://hg.mozilla.org/integration/autoland/json-pushes",
        json={"pushes": {"1": {"changesets": ["rev1"]}, "2": {"changesets": ["rev2"]}}},
    )
    for rev, backstop in (("rev1", True), ("rev2", False)):
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
    # The tasks come from the latest backstop push.
    responses.add(
        responses.GET,
        boot._decision_artifact_url("rev1", "task-graph.json"),
        json=TASK_GRAPH,
    )
    boot.retrieve_schedulable_tasks.retry_with(wait=tenacity.wait_none())()

    with open("known_tasks") as f:
        assert set(f.read().split("\n")) == KNOWN_TASKS
    with open("config_selection_tasks.json") as f:
        assert json.load(f) == {"backstop": "rev1", "tasks": CONFIG_SELECTION_TASKS}
