# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
from pathlib import Path

import pytest

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
