# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import datetime

from bugbug import repository, test_scheduling


def test_touched_together(monkeypatch):
    test_scheduling.touched_together = None

    repository.path_to_component = {
        "dom/file1.cpp": "Core::DOM",
        "dom/file2.cpp": "Core::DOM",
        "layout/file.cpp": "Core::Layout",
        "dom/tests/manifest1.ini": "Core::DOM",
        "dom/tests/manifest2.ini": "Core::DOM",
    }

    commits = {
        "commit1": repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        "commitbackedout": repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        "commit2": repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=("reviewer1",),
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
        "commit3": repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer2",),
        ).set_files(["layout/file.cpp", "dom/tests/manifest1.ini"], {}),
        "commit4": repository.Commit(
            node="commit4",
            author="author1",
            desc="commit4",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
    }

    def mock_get_commits():
        return (c.to_dict() for c in commits.values())

    monkeypatch.setattr(repository, "get_commits", mock_get_commits)

    update_touched_together_gen = test_scheduling.update_touched_together()
    next(update_touched_together_gen)

    update_touched_together_gen.send("commit2")

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

    update_touched_together_gen.send("commit4")

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


def test_touched_together_not_in_order(monkeypatch):
    test_scheduling.touched_together = None

    repository.path_to_component = {
        "dom/file1.cpp": "Core::DOM",
        "dom/file2.cpp": "Core::DOM",
        "layout/file.cpp": "Core::Layout",
        "dom/tests/manifest1.ini": "Core::DOM",
        "dom/tests/manifest2.ini": "Core::DOM",
    }

    commits = {
        "commit1": repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        "commitbackedout": repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        "commit2": repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=("reviewer1",),
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
        "commit3": repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer2",),
        ).set_files(["layout/file.cpp", "dom/tests/manifest1.ini"], {}),
        "commit4": repository.Commit(
            node="commit4",
            author="author1",
            desc="commit4",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
    }

    def mock_get_commits():
        return (c.to_dict() for c in commits.values())

    monkeypatch.setattr(repository, "get_commits", mock_get_commits)

    update_touched_together_gen = test_scheduling.update_touched_together()
    next(update_touched_together_gen)

    update_touched_together_gen.send("commit2")

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

    update_touched_together_gen.send("commit1")

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

    update_touched_together_gen.send("commit4")

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
