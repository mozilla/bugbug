# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import math
from datetime import datetime

import pytest
from _pytest.monkeypatch import MonkeyPatch

from bugbug import repository, test_scheduling
from bugbug.repository import CommitDict
from bugbug.test_scheduling import (
    ConfigGroup,
    Group,
    PushResult,
    Revision,
    Runnable,
    Task,
)
from bugbug.utils import ExpQueue


def test_rename_runnables() -> None:
    assert test_scheduling.rename_runnables(
        "label",
        (Task("test-linux64/opt-mochitest-browser-chrome-e10s-2"),),
    ) == (Task("test-linux1804-64/opt-mochitest-browser-chrome-e10s-2"),)
    assert test_scheduling.rename_runnables(
        "label",
        (Task("test-linux64-shippable/opt-mochitest-browser-chrome-e10s-2"),),
    ) == (Task("test-linux1804-64/opt-mochitest-browser-chrome-e10s-2"),)
    assert test_scheduling.rename_runnables(
        "label",
        (Task("test-linux64-shippable-qr/opt-mochitest-browser-chrome-e10s-2"),),
    ) == (Task("test-linux1804-64-qr/opt-mochitest-browser-chrome-e10s-2"),)
    assert test_scheduling.rename_runnables(
        "label",
        (
            Task("test-linux64/opt-mochitest-browser-chrome-e10s-2"),
            Task("test-linux64-qr/opt-web-platform-tests-wdspec-e10s-1"),
        ),
    ) == (
        Task("test-linux1804-64/opt-mochitest-browser-chrome-e10s-2"),
        Task("test-linux1804-64-qr/opt-web-platform-tests-wdspec-e10s-1"),
    )
    assert test_scheduling.rename_runnables(
        "label",
        (
            Task(
                "test-android-hw-p2-8-0-android-aarch64/pgo-geckoview-mochitest-media-e10s-2"
            ),
        ),
    ) == (
        Task(
            "test-android-hw-p2-8-0-android-aarch64/opt-geckoview-mochitest-media-e10s-2"
        ),
    )

    assert test_scheduling.rename_runnables(
        "group",
        (
            Group(
                "toolkit/components/extensions/test/mochitest/mochitest-remote.ini:toolkit/components/extensions/test/mochitest/mochitest-common.ini"
            ),
        ),
    ) == (Group("toolkit/components/extensions/test/mochitest/mochitest-remote.ini"),)
    assert test_scheduling.rename_runnables(
        "group", (Group("dom/prova/mochitest.ini"),)
    ) == (Group("dom/prova/mochitest.ini"),)

    assert test_scheduling.rename_runnables(
        "config_group",
        (
            ConfigGroup(
                (
                    "test-linux64-shippable/opt-*-e10s",
                    Group(
                        "toolkit/components/extensions/test/mochitest/mochitest-remote.ini:toolkit/components/extensions/test/mochitest/mochitest-common.ini"
                    ),
                )
            ),
        ),
    ) == (
        ConfigGroup(
            (
                "test-linux1804-64/opt-*-e10s",
                Group(
                    "toolkit/components/extensions/test/mochitest/mochitest-remote.ini"
                ),
            )
        ),
    )


def test_touched_together(monkeypatch: MonkeyPatch) -> None:
    test_scheduling.touched_together = None

    repository.path_to_component = {
        "dom/file1.cpp": "Core::DOM",
        "dom/file2.cpp": "Core::DOM",
        "layout/file.cpp": "Core::Layout",
        "dom/tests/manifest1.ini": "Core::DOM",
        "dom/tests/manifest2.ini": "Core::DOM",
    }

    commits = [
        repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1"],
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
        repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer2"],
        ).set_files(["layout/file.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit4",
            author="author1",
            desc="commit4",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
    ]
    commits = [c.to_dict() for c in commits]

    def mock_get_commits() -> list[CommitDict]:
        return commits

    monkeypatch.setattr(repository, "get_commits", mock_get_commits)

    update_touched_together_gen = test_scheduling.update_touched_together()
    next(update_touched_together_gen)

    update_touched_together_gen.send(Revision("commit2"))

    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom/tests", "dom/file1.cpp") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests/manifest1.ini") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom", "dom") == 0

    assert test_scheduling.get_touched_together("dom/file2.cpp", "layout/tests") == 1
    assert (
        test_scheduling.get_touched_together("dom", "layout/tests/manifest2.ini") == 1
    )
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1
    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/file2.cpp") == 0

    assert test_scheduling.get_touched_together("layout/file.cpp", "dom/tests") == 0
    assert test_scheduling.get_touched_together("layout", "dom/tests") == 0

    update_touched_together_gen.send(Revision("commit4"))

    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/tests") == 2
    assert test_scheduling.get_touched_together("dom/tests", "dom/file1.cpp") == 2
    assert test_scheduling.get_touched_together("dom", "dom/tests/manifest1.ini") == 2
    assert test_scheduling.get_touched_together("dom", "dom/tests") == 2
    assert test_scheduling.get_touched_together("dom", "dom") == 0

    assert test_scheduling.get_touched_together("dom/file2.cpp", "layout/tests") == 1
    assert (
        test_scheduling.get_touched_together("dom", "layout/tests/manifest2.ini") == 1
    )
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1
    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/file2.cpp") == 0

    assert test_scheduling.get_touched_together("layout/file.cpp", "dom/tests") == 1
    assert (
        test_scheduling.get_touched_together("layout", "dom/tests/manifest1.ini") == 1
    )
    assert test_scheduling.get_touched_together("layout", "dom/tests") == 1


def test_touched_together_restart(monkeypatch: MonkeyPatch) -> None:
    test_scheduling.touched_together = None

    repository.path_to_component = {
        "dom/file1.cpp": "Core::DOM",
        "dom/file2.cpp": "Core::DOM",
        "layout/file.cpp": "Core::Layout",
        "dom/tests/manifest1.ini": "Core::DOM",
        "dom/tests/manifest2.ini": "Core::DOM",
    }

    commits = [
        repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1"],
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
        repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer2"],
        ).set_files(["layout/file.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit4",
            author="author1",
            desc="commit4",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
    ]
    commits = [c.to_dict() for c in commits]

    def mock_get_commits() -> list[CommitDict]:
        return commits

    monkeypatch.setattr(repository, "get_commits", mock_get_commits)

    update_touched_together_gen = test_scheduling.update_touched_together()
    next(update_touched_together_gen)

    update_touched_together_gen.send(Revision("commit2"))

    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom/tests", "dom/file1.cpp") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests/manifest1.ini") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom", "dom") == 0

    assert test_scheduling.get_touched_together("dom/file2.cpp", "layout/tests") == 1
    assert (
        test_scheduling.get_touched_together("dom", "layout/tests/manifest2.ini") == 1
    )
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1
    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/file2.cpp") == 0

    assert test_scheduling.get_touched_together("layout/file.cpp", "dom/tests") == 0
    assert test_scheduling.get_touched_together("layout", "dom/tests") == 0

    try:
        update_touched_together_gen.send(None)
    except StopIteration:
        pass

    # Ensure we can still read the DB after closing.
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1
    test_scheduling.close_touched_together_db()

    update_touched_together_gen = test_scheduling.update_touched_together()
    next(update_touched_together_gen)

    update_touched_together_gen.send(Revision("commit4"))

    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/tests") == 2
    assert test_scheduling.get_touched_together("dom/tests", "dom/file1.cpp") == 2
    assert test_scheduling.get_touched_together("dom", "dom/tests/manifest1.ini") == 2
    assert test_scheduling.get_touched_together("dom", "dom/tests") == 2
    assert test_scheduling.get_touched_together("dom", "dom") == 0

    assert test_scheduling.get_touched_together("dom/file2.cpp", "layout/tests") == 1
    assert (
        test_scheduling.get_touched_together("dom", "layout/tests/manifest2.ini") == 1
    )
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1
    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/file2.cpp") == 0

    assert test_scheduling.get_touched_together("layout/file.cpp", "dom/tests") == 1
    assert (
        test_scheduling.get_touched_together("layout", "dom/tests/manifest1.ini") == 1
    )
    assert test_scheduling.get_touched_together("layout", "dom/tests") == 1


def test_touched_together_not_in_order(monkeypatch: MonkeyPatch) -> None:
    test_scheduling.touched_together = None

    repository.path_to_component = {
        "dom/file1.cpp": "Core::DOM",
        "dom/file2.cpp": "Core::DOM",
        "layout/file.cpp": "Core::Layout",
        "dom/tests/manifest1.ini": "Core::DOM",
        "dom/tests/manifest2.ini": "Core::DOM",
    }

    commits = [
        repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1"],
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
        repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer2"],
        ).set_files(["layout/file.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit4",
            author="author1",
            desc="commit4",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
    ]
    commits = [c.to_dict() for c in commits]

    def mock_get_commits() -> list[CommitDict]:
        return commits

    monkeypatch.setattr(repository, "get_commits", mock_get_commits)

    update_touched_together_gen = test_scheduling.update_touched_together()
    next(update_touched_together_gen)

    update_touched_together_gen.send(Revision("commit2"))

    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom/tests", "dom/file1.cpp") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests/manifest1.ini") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom", "dom") == 0

    assert test_scheduling.get_touched_together("dom/file2.cpp", "layout/tests") == 1
    assert (
        test_scheduling.get_touched_together("dom", "layout/tests/manifest2.ini") == 1
    )
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1
    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/file2.cpp") == 0

    assert test_scheduling.get_touched_together("layout/file.cpp", "dom/tests") == 0
    assert test_scheduling.get_touched_together("layout", "dom/tests") == 0

    update_touched_together_gen.send(Revision("commit1"))

    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom/tests", "dom/file1.cpp") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests/manifest1.ini") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom", "dom") == 0

    assert test_scheduling.get_touched_together("dom/file2.cpp", "layout/tests") == 1
    assert (
        test_scheduling.get_touched_together("dom", "layout/tests/manifest2.ini") == 1
    )
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1
    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/file2.cpp") == 0

    assert test_scheduling.get_touched_together("layout/file.cpp", "dom/tests") == 0
    assert test_scheduling.get_touched_together("layout", "dom/tests") == 0

    update_touched_together_gen.send(Revision("commit4"))

    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/tests") == 2
    assert test_scheduling.get_touched_together("dom/tests", "dom/file1.cpp") == 2
    assert test_scheduling.get_touched_together("dom", "dom/tests/manifest1.ini") == 2
    assert test_scheduling.get_touched_together("dom", "dom/tests") == 2
    assert test_scheduling.get_touched_together("dom", "dom") == 0

    assert test_scheduling.get_touched_together("dom/file2.cpp", "layout/tests") == 1
    assert (
        test_scheduling.get_touched_together("dom", "layout/tests/manifest2.ini") == 1
    )
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1
    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/file2.cpp") == 0

    assert test_scheduling.get_touched_together("layout/file.cpp", "dom/tests") == 1
    assert (
        test_scheduling.get_touched_together("layout", "dom/tests/manifest1.ini") == 1
    )
    assert test_scheduling.get_touched_together("layout", "dom/tests") == 1


def test_touched_together_with_backout(monkeypatch: MonkeyPatch) -> None:
    test_scheduling.touched_together = None

    repository.path_to_component = {
        "dom/file1.cpp": "Core::DOM",
        "dom/file2.cpp": "Core::DOM",
        "layout/file.cpp": "Core::Layout",
        "dom/tests/manifest1.ini": "Core::DOM",
        "dom/tests/manifest2.ini": "Core::DOM",
    }

    commits = [
        repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1"],
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
    ]
    commits = [c.to_dict() for c in commits]

    def mock_get_commits() -> list[CommitDict]:
        return commits

    monkeypatch.setattr(repository, "get_commits", mock_get_commits)

    update_touched_together_gen = test_scheduling.update_touched_together()
    next(update_touched_together_gen)

    update_touched_together_gen.send(Revision("commitbackedout"))

    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom/tests", "dom/file1.cpp") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests/manifest1.ini") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom", "dom") == 0

    assert test_scheduling.get_touched_together("dom/file2.cpp", "layout/tests") == 0
    assert (
        test_scheduling.get_touched_together("dom", "layout/tests/manifest2.ini") == 0
    )
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 0
    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/file2.cpp") == 0

    assert test_scheduling.get_touched_together("layout/file.cpp", "dom/tests") == 0
    assert test_scheduling.get_touched_together("layout", "dom/tests") == 0

    update_touched_together_gen.send(Revision("commit2"))

    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom/tests", "dom/file1.cpp") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests/manifest1.ini") == 1
    assert test_scheduling.get_touched_together("dom", "dom/tests") == 1
    assert test_scheduling.get_touched_together("dom", "dom") == 0

    assert test_scheduling.get_touched_together("dom/file2.cpp", "layout/tests") == 1
    assert (
        test_scheduling.get_touched_together("dom", "layout/tests/manifest2.ini") == 1
    )
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1
    assert test_scheduling.get_touched_together("dom/file1.cpp", "dom/file2.cpp") == 0

    assert test_scheduling.get_touched_together("layout/file.cpp", "dom/tests") == 0
    assert test_scheduling.get_touched_together("layout", "dom/tests") == 0


def test_cochange_pmi(monkeypatch: MonkeyPatch) -> None:
    test_scheduling.touched_together = None

    commits = [
        {
            "node": "commit1",
            "backedoutby": "",
            "files": ["dom/file1.cpp", "dom/tests/manifest1.ini"],
        },
        {
            "node": "commitbackedout",
            "backedoutby": "commitbackout",
            "files": ["dom/file1.cpp", "layout/tests/manifest2.ini"],
        },
        {
            "node": "commit2",
            "backedoutby": "",
            "files": ["dom/file2.cpp", "layout/tests/manifest2.ini"],
        },
        {
            "node": "commit3",
            "backedoutby": "",
            "files": ["layout/file.cpp", "dom/tests/sub/test.js"],
        },
        {
            "node": "commit4",
            "backedoutby": "",
            "files": ["dom/file1.cpp", "dom/tests/manifest1.ini"],
        },
    ]
    monkeypatch.setattr(repository, "get_commits", lambda: commits)

    update_touched_together_gen = test_scheduling.update_touched_together(
        {"dom/tests", "layout/tests"}
    )
    next(update_touched_together_gen)
    update_touched_together_gen.send(Revision("commit4"))
    try:
        update_touched_together_gen.send(None)
    except StopIteration:
        pass

    # 4 commits (the backed-out one is skipped); dom was changed in 3 of them, layout in 1, dom/tests
    # (including its subdirectories) in 3, layout/tests in 1.
    assert test_scheduling.get_cochange_pmi(["layout"], ["dom/tests"]) == pytest.approx(
        math.log(1 * 4 / (1 * 3))
    )
    assert test_scheduling.get_cochange_pmi(["dom"], ["layout/tests"]) == pytest.approx(
        math.log(1 * 4 / (3 * 1))
    )
    # Changed together less often than by chance: 2 * 4 / (3 * 3) < 1.
    assert test_scheduling.get_cochange_pmi(["dom"], ["dom/tests"]) == 0.0
    # Never changed together.
    assert test_scheduling.get_cochange_pmi(["layout"], ["layout/tests"]) == 0.0
    assert test_scheduling.get_cochange_pmi(["toolkit"], ["dom/tests"]) == 0.0
    # The maximum over the directories.
    assert test_scheduling.get_cochange_pmi(
        ["dom", "layout"], ["dom/tests", "layout/tests"]
    ) == pytest.approx(math.log(4 / 3))


@pytest.mark.parametrize("granularity", ["group", "label"])
def test_generate_data(granularity: str) -> None:
    past_failures = test_scheduling.PastFailures(granularity, False)

    commits = [
        CommitDict(
            {
                "types": ["C/C++"],
                "files": ["dom/file1.cpp"],
                "directories": ["dom"],
                "components": ["DOM"],
            }
        ),
        CommitDict(
            {
                "types": ["C/C++"],
                "files": ["dom/file1.cpp", "dom/file2.cpp"],
                "directories": ["dom"],
                "components": ["DOM"],
            }
        ),
        CommitDict(
            {
                "types": ["C/C++"],
                "files": ["layout/file.cpp"],
                "directories": ["layout"],
                "components": ["Layout"],
            }
        ),
        CommitDict(
            {
                "types": ["C/C++"],
                "files": ["layout/file.cpp"],
                "directories": ["layout"],
                "components": ["Layout"],
            }
        ),
        CommitDict(
            {
                "types": ["JavaScript", "C/C++"],
                "files": ["dom/file1.cpp", "dom/file1.js"],
                "directories": ["dom"],
                "components": ["DOM"],
            }
        ),
    ]

    data = list(
        test_scheduling.generate_data(
            granularity,
            past_failures,
            commits[0],
            1,
            ["runnable1", "runnable2"],
            [],
            [],
        )
    )
    assert len(data) == 2
    obj = {
        "failures": 0,
        "failures_in_components": 0,
        "failures_in_directories": 0,
        "failures_in_files": 0,
        "failures_in_types": 0,
        "failures_past_1400_pushes": 0,
        "failures_past_1400_pushes_in_components": 0,
        "failures_past_1400_pushes_in_directories": 0,
        "failures_past_1400_pushes_in_files": 0,
        "failures_past_1400_pushes_in_types": 0,
        "failures_past_2800_pushes": 0,
        "failures_past_2800_pushes_in_components": 0,
        "failures_past_2800_pushes_in_directories": 0,
        "failures_past_2800_pushes_in_files": 0,
        "failures_past_2800_pushes_in_types": 0,
        "failures_past_700_pushes": 0,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 0,
        "is_likely_regression": False,
        "is_possible_regression": False,
        "name": "runnable1",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[0] == obj

    obj = {
        "failures": 0,
        "failures_in_components": 0,
        "failures_in_directories": 0,
        "failures_in_files": 0,
        "failures_in_types": 0,
        "failures_past_1400_pushes": 0,
        "failures_past_1400_pushes_in_components": 0,
        "failures_past_1400_pushes_in_directories": 0,
        "failures_past_1400_pushes_in_files": 0,
        "failures_past_1400_pushes_in_types": 0,
        "failures_past_2800_pushes": 0,
        "failures_past_2800_pushes_in_components": 0,
        "failures_past_2800_pushes_in_directories": 0,
        "failures_past_2800_pushes_in_files": 0,
        "failures_past_2800_pushes_in_types": 0,
        "failures_past_700_pushes": 0,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 0,
        "is_likely_regression": False,
        "is_possible_regression": False,
        "name": "runnable2",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[1] == obj

    data = list(
        test_scheduling.generate_data(
            granularity,
            past_failures,
            commits[1],
            2,
            ["runnable1", "runnable2"],
            ["runnable1"],
            [],
        )
    )
    assert len(data) == 2
    obj = {
        "failures": 0,
        "failures_in_components": 0,
        "failures_in_directories": 0,
        "failures_in_files": 0,
        "failures_in_types": 0,
        "failures_past_1400_pushes": 0,
        "failures_past_1400_pushes_in_components": 0,
        "failures_past_1400_pushes_in_directories": 0,
        "failures_past_1400_pushes_in_files": 0,
        "failures_past_1400_pushes_in_types": 0,
        "failures_past_2800_pushes": 0,
        "failures_past_2800_pushes_in_components": 0,
        "failures_past_2800_pushes_in_directories": 0,
        "failures_past_2800_pushes_in_files": 0,
        "failures_past_2800_pushes_in_types": 0,
        "failures_past_700_pushes": 0,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 0,
        "is_likely_regression": False,
        "is_possible_regression": True,
        "name": "runnable1",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[0] == obj
    obj = {
        "failures": 0,
        "failures_in_components": 0,
        "failures_in_directories": 0,
        "failures_in_files": 0,
        "failures_in_types": 0,
        "failures_past_1400_pushes": 0,
        "failures_past_1400_pushes_in_components": 0,
        "failures_past_1400_pushes_in_directories": 0,
        "failures_past_1400_pushes_in_files": 0,
        "failures_past_1400_pushes_in_types": 0,
        "failures_past_2800_pushes": 0,
        "failures_past_2800_pushes_in_components": 0,
        "failures_past_2800_pushes_in_directories": 0,
        "failures_past_2800_pushes_in_files": 0,
        "failures_past_2800_pushes_in_types": 0,
        "failures_past_700_pushes": 0,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 0,
        "is_likely_regression": False,
        "is_possible_regression": False,
        "name": "runnable2",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[1] == obj

    data = list(
        test_scheduling.generate_data(
            granularity,
            past_failures,
            commits[2],
            3,
            ["runnable1", "runnable2"],
            [],
            ["runnable2"],
        )
    )
    assert len(data) == 2
    obj = {
        "failures": 1,
        "failures_in_components": 0,
        "failures_in_directories": 0,
        "failures_in_files": 0,
        "failures_in_types": 1,
        "failures_past_1400_pushes": 1,
        "failures_past_1400_pushes_in_components": 0,
        "failures_past_1400_pushes_in_directories": 0,
        "failures_past_1400_pushes_in_files": 0,
        "failures_past_1400_pushes_in_types": 1,
        "failures_past_2800_pushes": 1,
        "failures_past_2800_pushes_in_components": 0,
        "failures_past_2800_pushes_in_directories": 0,
        "failures_past_2800_pushes_in_files": 0,
        "failures_past_2800_pushes_in_types": 1,
        "failures_past_700_pushes": 1,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 1,
        "is_likely_regression": False,
        "is_possible_regression": False,
        "name": "runnable1",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[0] == obj
    obj = {
        "failures": 0,
        "failures_in_components": 0,
        "failures_in_directories": 0,
        "failures_in_files": 0,
        "failures_in_types": 0,
        "failures_past_1400_pushes": 0,
        "failures_past_1400_pushes_in_components": 0,
        "failures_past_1400_pushes_in_directories": 0,
        "failures_past_1400_pushes_in_files": 0,
        "failures_past_1400_pushes_in_types": 0,
        "failures_past_2800_pushes": 0,
        "failures_past_2800_pushes_in_components": 0,
        "failures_past_2800_pushes_in_directories": 0,
        "failures_past_2800_pushes_in_files": 0,
        "failures_past_2800_pushes_in_types": 0,
        "failures_past_700_pushes": 0,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 0,
        "is_likely_regression": True,
        "is_possible_regression": False,
        "name": "runnable2",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[1] == obj

    data = list(
        test_scheduling.generate_data(
            granularity, past_failures, commits[3], 4, ["runnable1"], [], []
        )
    )
    assert len(data) == 1
    obj = {
        "failures": 1,
        "failures_in_components": 0,
        "failures_in_directories": 0,
        "failures_in_files": 0,
        "failures_in_types": 1,
        "failures_past_1400_pushes": 1,
        "failures_past_1400_pushes_in_components": 0,
        "failures_past_1400_pushes_in_directories": 0,
        "failures_past_1400_pushes_in_files": 0,
        "failures_past_1400_pushes_in_types": 1,
        "failures_past_2800_pushes": 1,
        "failures_past_2800_pushes_in_components": 0,
        "failures_past_2800_pushes_in_directories": 0,
        "failures_past_2800_pushes_in_files": 0,
        "failures_past_2800_pushes_in_types": 1,
        "failures_past_700_pushes": 1,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 1,
        "is_likely_regression": False,
        "is_possible_regression": False,
        "name": "runnable1",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[0] == obj

    data = list(
        test_scheduling.generate_data(
            granularity,
            past_failures,
            commits[4],
            1500,
            ["runnable1", "runnable2"],
            ["runnable1", "runnable2"],
            [],
        )
    )
    assert len(data) == 2
    obj = {
        "failures": 1,
        "failures_in_components": 1,
        "failures_in_directories": 1,
        "failures_in_files": 1,
        "failures_in_types": 1,
        "failures_past_1400_pushes": 0,
        "failures_past_1400_pushes_in_components": 0,
        "failures_past_1400_pushes_in_directories": 0,
        "failures_past_1400_pushes_in_files": 0,
        "failures_past_1400_pushes_in_types": 0,
        "failures_past_2800_pushes": 1,
        "failures_past_2800_pushes_in_components": 1,
        "failures_past_2800_pushes_in_directories": 1,
        "failures_past_2800_pushes_in_files": 1,
        "failures_past_2800_pushes_in_types": 1,
        "failures_past_700_pushes": 0,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 0,
        "is_likely_regression": False,
        "is_possible_regression": True,
        "name": "runnable1",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[0] == obj
    obj = {
        "failures": 1,
        "failures_in_components": 0,
        "failures_in_directories": 0,
        "failures_in_files": 0,
        "failures_in_types": 1,
        "failures_past_1400_pushes": 0,
        "failures_past_1400_pushes_in_components": 0,
        "failures_past_1400_pushes_in_directories": 0,
        "failures_past_1400_pushes_in_files": 0,
        "failures_past_1400_pushes_in_types": 0,
        "failures_past_2800_pushes": 1,
        "failures_past_2800_pushes_in_components": 0,
        "failures_past_2800_pushes_in_directories": 0,
        "failures_past_2800_pushes_in_files": 0,
        "failures_past_2800_pushes_in_types": 1,
        "failures_past_700_pushes": 0,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 0,
        "is_likely_regression": False,
        "is_possible_regression": True,
        "name": "runnable2",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[1] == obj

    data = list(
        test_scheduling.generate_data(
            granularity,
            past_failures,
            commits[4],
            2400,
            ["runnable1", "runnable2"],
            ["runnable1", "runnable2"],
            [],
        )
    )
    assert len(data) == 2
    obj = {
        "failures": 2,
        "failures_in_components": 2,
        "failures_in_directories": 2,
        "failures_in_files": 3,
        "failures_in_types": 3,
        "failures_past_1400_pushes": 1,
        "failures_past_1400_pushes_in_components": 1,
        "failures_past_1400_pushes_in_directories": 1,
        "failures_past_1400_pushes_in_files": 2,
        "failures_past_1400_pushes_in_types": 2,
        "failures_past_2800_pushes": 2,
        "failures_past_2800_pushes_in_components": 2,
        "failures_past_2800_pushes_in_directories": 2,
        "failures_past_2800_pushes_in_files": 3,
        "failures_past_2800_pushes_in_types": 3,
        "failures_past_700_pushes": 0,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 0,
        "is_likely_regression": False,
        "is_possible_regression": True,
        "name": "runnable1",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[0] == obj
    obj = {
        "failures": 2,
        "failures_in_components": 1,
        "failures_in_directories": 1,
        "failures_in_files": 2,
        "failures_in_types": 3,
        "failures_past_1400_pushes": 1,
        "failures_past_1400_pushes_in_components": 1,
        "failures_past_1400_pushes_in_directories": 1,
        "failures_past_1400_pushes_in_files": 2,
        "failures_past_1400_pushes_in_types": 2,
        "failures_past_2800_pushes": 2,
        "failures_past_2800_pushes_in_components": 1,
        "failures_past_2800_pushes_in_directories": 1,
        "failures_past_2800_pushes_in_files": 2,
        "failures_past_2800_pushes_in_types": 3,
        "failures_past_700_pushes": 0,
        "failures_past_700_pushes_in_components": 0,
        "failures_past_700_pushes_in_directories": 0,
        "failures_past_700_pushes_in_files": 0,
        "failures_past_700_pushes_in_types": 0,
        "is_likely_regression": False,
        "is_possible_regression": True,
        "name": "runnable2",
    }
    if granularity == "group":
        obj["touched_together_directories"] = 0
        obj["touched_together_pmi"] = 0.0
        obj["touched_together_files"] = 0
    assert data[1] == obj


def test_index_runs() -> None:
    push_data: list[PushResult] = [
        ((Revision("r0"),), Revision("f"), (Group("a"), Group("b")), (), ()),
        ((Revision("r1"),), Revision("f"), (Group("b"),), (), ()),
        ((Revision("r3"),), Revision("f"), (Group("a"),), (), ()),
    ]
    runs, rev_to_push = test_scheduling.index_runs(push_data)
    assert runs == {"a": [0, 2], "b": [0, 1]}
    assert rev_to_push == {"r0": 0, "r1": 1, "r3": 2}


def test_get_non_run_negatives() -> None:
    import random

    runs: dict[Runnable, list[int]] = {
        # Ran on the push itself: excluded by the caller.
        Group("ran"): [5],
        # Ran on a later push before the end: verified.
        Group("later"): [2, 7],
        # Only ran after the end (e.g. after the backout): unknown.
        Group("too_late"): [12],
        # Never ran after the push: unknown.
        Group("before"): [1, 3],
    }
    candidates: list[Runnable] = [
        Group("ran"),
        Group("later"),
        Group("too_late"),
        Group("before"),
    ]
    assert test_scheduling.get_non_run_negatives(
        5, 10, candidates, {Group("ran")}, runs, 10, random.Random(0)
    ) == [Group("later")]
    # With a count of 0, nothing is sampled.
    assert (
        test_scheduling.get_non_run_negatives(
            5, 10, candidates, {Group("ran")}, runs, 0, random.Random(0)
        )
        == []
    )


def test_filter_runnables_ignores_jstests() -> None:
    jstests = Group("tests/jsreftest/tests/js/src/tests/jstests.list")
    mochitest = Group("dom/base/test/mochitest.toml")
    groups = (jstests, mochitest)
    assert test_scheduling.filter_runnables(groups, set(groups), "group") == (
        mochitest,
    )

    config_groups = (
        ConfigGroup(("test-linux1804-64/opt-*", jstests)),
        ConfigGroup(("test-linux1804-64/opt-*", mochitest)),
    )
    assert test_scheduling.filter_runnables(
        config_groups, set(config_groups), "config_group"
    ) == (config_groups[1],)


def test_get_runnable_dirs() -> None:
    assert test_scheduling.get_runnable_dirs("dom/base/test/mochitest.toml") == (
        "dom/base/test",
    )
    assert test_scheduling.get_runnable_dirs("layout/reftests/bugs/reftest.list") == (
        "layout/reftests/bugs",
    )
    assert test_scheduling.get_runnable_dirs(
        "testing/web-platform/tests/css/css-grid"
    ) == (
        "testing/web-platform/tests/css/css-grid",
        "testing/web-platform/meta/css/css-grid",
    )
    assert test_scheduling.get_runnable_dirs(
        "testing/web-platform/mozilla/tests/webgpu"
    ) == (
        "testing/web-platform/mozilla/tests/webgpu",
        "testing/web-platform/mozilla/meta/webgpu",
    )


def test_fallback_on_ini() -> None:
    past_failures = test_scheduling.PastFailures("group", False)

    past_failures.set("browser.ini", ExpQueue(0, 1, 42))
    past_failures.set("reftest.list", ExpQueue(0, 1, 7))

    def assert_val(manifest, val):
        exp_queue = past_failures.get(manifest)
        assert exp_queue is not None
        assert exp_queue[0] == val

    assert_val("browser.ini", 42)
    assert_val("browser.toml", 42)
    assert_val("reftest.list", 7)
    assert past_failures.get("reftest.toml") is None
    assert past_failures.get("unexisting.ini") is None

    past_failures.set("browser.toml", ExpQueue(0, 1, 22))
    assert_val("browser.toml", 22)
    assert_val("browser.ini", 42)


def test_find_manifests_for_paths(tmp_path) -> None:
    (tmp_path / "dom" / "battery" / "test").mkdir(parents=True)
    (tmp_path / "dom" / "battery" / "test" / "mochitest.toml").touch()
    (tmp_path / "dom" / "battery" / "test" / "chrome.toml").touch()

    manifest = """[DEFAULT]
head = "../prova.js"
support-files = [
  "!/absolute_path_with_glob/*.js",
  "!/absolute_path_with_subdirglob/**",
  "relative/path.png"
]

["test_resolve_uris_ipc.js"]
"""

    manifest2 = """[DEFAULT]
head = ""
support-files = ""

["test_resolve_uris_ipc.js"]
"""

    (tmp_path / "test").mkdir(parents=True)
    (tmp_path / "test" / "chrome.toml").write_text(manifest)
    (tmp_path / "test" / "mochitest.toml").write_text(manifest2)
    (tmp_path / "absolute_path_with_glob" / "subdir").mkdir(parents=True)
    (tmp_path / "absolute_path_with_glob" / "asd.js").touch()
    (tmp_path / "absolute_path_with_glob" / "asd.png").touch()
    (tmp_path / "absolute_path_with_glob" / "subdir" / "asd.js").touch()
    (tmp_path / "absolute_path_with_subdirglob" / "subdir").mkdir(parents=True)
    (tmp_path / "absolute_path_with_subdirglob" / "asd.js").touch()
    (tmp_path / "absolute_path_with_subdirglob" / "subdir" / "asd.js").touch()

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path), ["dom/battery/BatteryManager.cpp"]
    ) == {
        "dom/battery/test/mochitest.toml",
        "dom/battery/test/chrome.toml",
    }

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path), ["dom/battery/BatteryManager.cpp", "test/chrome.toml"]
    ) == {
        "dom/battery/test/mochitest.toml",
        "dom/battery/test/chrome.toml",
        "test/chrome.toml",
    }

    assert test_scheduling.find_manifests_for_paths(str(tmp_path), ["prova.js"]) == {
        "test/chrome.toml"
    }

    # A root-level file that is not referenced by any manifest must not
    # schedule every manifest in the repository.
    (tmp_path / "mach").touch()
    assert test_scheduling.find_manifests_for_paths(str(tmp_path), ["mach"]) == set()

    # A file close to too many manifests (e.g. dom/moz.build) must not
    # schedule all of them.
    (tmp_path / "hub" / "moz.build").parent.mkdir(parents=True)
    (tmp_path / "hub" / "moz.build").touch()
    for i in range(test_scheduling.MAX_SIBLING_MANIFESTS):
        (tmp_path / "hub" / f"component{i}" / "test").mkdir(parents=True)
        (tmp_path / "hub" / f"component{i}" / "test" / "mochitest.toml").touch()

    assert (
        len(test_scheduling.find_manifests_for_paths(str(tmp_path), ["hub/moz.build"]))
        == test_scheduling.MAX_SIBLING_MANIFESTS
    )

    (tmp_path / "hub" / "one_more" / "test").mkdir(parents=True)
    (tmp_path / "hub" / "one_more" / "test" / "mochitest.toml").touch()

    assert (
        test_scheduling.find_manifests_for_paths(str(tmp_path), ["hub/moz.build"])
        == set()
    )

    # The cap applies per path, so a narrow file is still scheduled when
    # modified together with a broad one.
    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path), ["hub/moz.build", "dom/battery/BatteryManager.cpp"]
    ) == {
        "dom/battery/test/mochitest.toml",
        "dom/battery/test/chrome.toml",
    }

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path), ["test/test_resolve_uris_ipc.js"]
    ) == {
        "test/chrome.toml",
        "test/mochitest.toml",
    }

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path), ["test/relative/path.png"]
    ) == {"test/chrome.toml"}

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path), ["absolute_path_with_glob/asd.js"]
    ) == {"test/chrome.toml"}

    assert (
        test_scheduling.find_manifests_for_paths(
            str(tmp_path), ["absolute_path_with_glob/asd.png"]
        )
        == set()
    )

    assert (
        test_scheduling.find_manifests_for_paths(
            str(tmp_path), ["absolute_path_with_glob/subdir/asd.js"]
        )
        == set()
    )

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path), ["absolute_path_with_subdirglob/asd.js"]
    ) == {"test/chrome.toml"}

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path), ["absolute_path_with_subdirglob/subdir/asd.js"]
    ) == {"test/chrome.toml"}

    (tmp_path / "testing/web-platform/tests/html/semantics").mkdir(parents=True)
    (tmp_path / "testing/web-platform/tests/.gitignore").touch()
    (
        tmp_path
        / "testing/web-platform/tests/html/semantics/rellist-feature-detection.html"
    ).touch()
    (tmp_path / "testing/web-platform/tests/html/semantics/META.yml").touch()
    (tmp_path / "testing/web-platform/tests/html/semantics/interactive-elements").mkdir(
        parents=True
    )
    (
        tmp_path
        / "testing/web-platform/tests/html/semantics/interactive-elements"
        / "contextmenu-historical.html"
    ).touch()
    (tmp_path / "testing/web-platform/mozilla/meta/pointerevents").mkdir(parents=True)
    (
        tmp_path
        / "testing/web-platform/mozilla/meta/pointerevents/pointerevent_click_during_parent_capture.html.ini"
    ).touch()
    (tmp_path / "testing/web-platform/mozilla/tests/pointerevents").mkdir(parents=True)
    (
        tmp_path
        / "testing/web-platform/mozilla/tests/pointerevents/pointerevent_click_during_parent_capture.html"
    ).touch()
    (tmp_path / "testing/web-platform/tests/encrypted-media/content").mkdir(
        parents=True
    )
    (
        tmp_path
        / "testing/web-platform/tests/encrypted-media/clearkey-events.https.html"
    ).touch()
    (
        tmp_path
        / "testing/web-platform/tests/encrypted-media/content/content-metadata.js"
    ).touch()

    assert (
        test_scheduling.find_manifests_for_paths(
            str(tmp_path), ["testing/web-platform/tests/.gitignore"]
        )
        == set()
    )

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path),
        ["testing/web-platform/tests/html/semantics/rellist-feature-detection.html"],
    ) == {"testing/web-platform/tests/html/semantics"}

    assert (
        test_scheduling.find_manifests_for_paths(
            str(tmp_path), ["testing/web-platform/tests/html/semantics/META.yml"]
        )
        == set()
    )

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path),
        [
            "testing/web-platform/tests/html/semantics/interactive-elements/contextmenu-historical.html"
        ],
    ) == {"testing/web-platform/tests/html/semantics/interactive-elements"}

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path),
        [
            "testing/web-platform/mozilla/meta/pointerevents/pointerevent_click_during_parent_capture.html.ini"
        ],
    ) == {"testing/web-platform/mozilla/tests/pointerevents"}

    assert test_scheduling.find_manifests_for_paths(
        str(tmp_path),
        ["testing/web-platform/tests/encrypted-media/content/content-metadata.js"],
    ) == {"testing/web-platform/tests/encrypted-media"}


def test_find_tasks_for_paths(tmp_path) -> None:
    known_tasks = (
        "test-linux64/opt-gtest-1proc",
        "test-linux64/opt-mochitest-browser-chrome-1proc",
        "test-linux64/opt-gtest-e10s",
        "test-linux64/opt-cppunit",
        "test-linux64/opt-rusttests",
    )

    # Set up a minimal cppunittest.toml listing two test names.
    (tmp_path / "testing").mkdir(parents=True)
    (tmp_path / "testing" / "cppunittest.toml").write_text(
        '[DEFAULT]\n\n["TestArray"]\n\n["TestArrayUtils"]\n'
    )

    # Non-C/C++ file containing GTest patterns should not trigger gtest selection.
    (tmp_path / "script.py").write_bytes(b"TEST(Foo, Bar) {}")
    assert (
        test_scheduling.find_tasks_for_paths(str(tmp_path), known_tasks, ["script.py"])
        == []
    )

    # C/C++ file without GTest patterns should not trigger gtest selection.
    (tmp_path / "source.cpp").write_bytes(b"int main() { return 0; }")
    assert (
        test_scheduling.find_tasks_for_paths(str(tmp_path), known_tasks, ["source.cpp"])
        == []
    )

    # C/C++ file with TEST macro selects tasks containing "gtest".
    (tmp_path / "test_foo.cpp").write_bytes(b"TEST(FooTest, Bar) {}\n")
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["test_foo.cpp"]
    ) == [
        "test-linux64/opt-gtest-1proc",
        "test-linux64/opt-gtest-e10s",
    ]

    # C/C++ file with TEST_F macro also triggers gtest selection.
    (tmp_path / "test_fixture.cpp").write_bytes(b"TEST_F(FooFixture, Bar) {}\n")
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["test_fixture.cpp"]
    ) == [
        "test-linux64/opt-gtest-1proc",
        "test-linux64/opt-gtest-e10s",
    ]

    # Non-existent C/C++ file raises OSError which is silently skipped.
    assert (
        test_scheduling.find_tasks_for_paths(
            str(tmp_path), known_tasks, ["nonexistent.cpp"]
        )
        == []
    )

    # No paths -> no tasks selected.
    assert test_scheduling.find_tasks_for_paths(str(tmp_path), known_tasks, []) == []

    # known_tasks without any "gtest" task -> empty even when GTest file present.
    assert (
        test_scheduling.find_tasks_for_paths(
            str(tmp_path),
            ("test-linux64/opt-mochitest-browser-chrome-1proc",),
            ["test_foo.cpp"],
        )
        == []
    )

    # Two paths: one C/C++ with GTest patterns and one without -> gtest tasks selected.
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["source.cpp", "test_foo.cpp"]
    ) == [
        "test-linux64/opt-gtest-1proc",
        "test-linux64/opt-gtest-e10s",
    ]

    # File whose path contains a gtest folder triggers gtest selection regardless of content.
    (tmp_path / "dom" / "media" / "gtest").mkdir(parents=True)
    (tmp_path / "dom" / "media" / "gtest" / "TestCubeb.cpp").write_bytes(
        b"// no test macros\n"
    )
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["dom/media/gtest/TestCubeb.cpp"]
    ) == [
        "test-linux64/opt-gtest-1proc",
        "test-linux64/opt-gtest-e10s",
    ]

    # File in a folder adjacent to a gtest subfolder triggers gtest selection.
    (tmp_path / "dom" / "media" / "CubebUtils.cpp").write_bytes(b"int foo() {}\n")
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["dom/media/CubebUtils.cpp"]
    ) == [
        "test-linux64/opt-gtest-1proc",
        "test-linux64/opt-gtest-e10s",
    ]

    # Rust file triggers rusttests selection.
    (tmp_path / "servo").mkdir()
    (tmp_path / "servo" / "lib.rs").write_bytes(b"pub fn foo() {}\n")
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["servo/lib.rs"]
    ) == ["test-linux64/opt-rusttests"]

    # Non-Rust file does not trigger rusttests selection.
    assert "test-linux64/opt-rusttests" not in test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["servo/foo.cpp"]
    )

    # Modifying testing/cppunittest.toml itself triggers cppunit selection.
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["testing/cppunittest.toml"]
    ) == ["test-linux64/opt-cppunit"]

    # Modifying testing/remotecppunittests.py triggers cppunit selection.
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["testing/remotecppunittests.py"]
    ) == ["test-linux64/opt-cppunit"]

    # Modifying testing/runcppunittests.py triggers cppunit selection.
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["testing/runcppunittests.py"]
    ) == ["test-linux64/opt-cppunit"]

    # A .cpp file whose stem is listed in cppunittest.toml triggers cppunit selection.
    assert test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["mfbt/TestArrayUtils.cpp"]
    ) == ["test-linux64/opt-cppunit"]

    # A .cpp file whose stem is not listed in cppunittest.toml does not trigger cppunit.
    assert "test-linux64/opt-cppunit" not in test_scheduling.find_tasks_for_paths(
        str(tmp_path), known_tasks, ["mfbt/TestUnknown.cpp"]
    )

    # Empty known_tasks -> always empty.
    assert (
        test_scheduling.find_tasks_for_paths(str(tmp_path), (), ["test_foo.cpp"]) == []
    )


def test_get_suite_test_dirs(tmp_path) -> None:
    def write(path, content):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(content)

    write(
        "browser/components/moz.build",
        'MARIONETTE_MANIFESTS += ["sessionstore/test/marionette/manifest.toml"]\n',
    )
    write(
        "toolkit/components/telemetry/moz.build",
        "TELEMETRY_TESTS_CLIENT_MANIFESTS += [\n"
        '    "tests/marionette/tests/manifest.toml",\n'
        "]\n",
    )
    write("xpcom/tests/gtest/moz.build", 'FINAL_LIBRARY = "xul-gtest"\n')
    write("mfbt/tests/moz.build", 'CppUnitTests(["TestArray"])\n')
    write("dom/base/moz.build", 'MOCHITEST_MANIFESTS += ["test/mochitest.toml"]\n')
    # Third-party code and object directories are skipped.
    write("third_party/foo/moz.build", 'FINAL_LIBRARY = "xul-gtest"\n')
    write("obj-x86_64-pc-linux-gnu/moz.build", 'FINAL_LIBRARY = "xul-gtest"\n')

    assert test_scheduling.get_suite_test_dirs(str(tmp_path)) == {
        "marionette": ("browser/components/sessionstore/test/marionette/",),
        "telemetry-tests": ("toolkit/components/telemetry/tests/marionette/tests/",),
        "firefox-ui": (),
        "gtest": ("xpcom/tests/gtest/",),
        "cppunittest": ("mfbt/tests/",),
    }
