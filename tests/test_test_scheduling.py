# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import datetime

import pytest

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

    commits = [
        repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=("reviewer1",),
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
        repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer2",),
        ).set_files(["layout/file.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit4",
            author="author1",
            desc="commit4",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
    ]
    commits = [c.to_dict() for c in commits]

    def mock_get_commits():
        return commits

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


def test_touched_together_restart(monkeypatch):
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
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=("reviewer1",),
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
        repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer2",),
        ).set_files(["layout/file.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit4",
            author="author1",
            desc="commit4",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
    ]
    commits = [c.to_dict() for c in commits]

    def mock_get_commits():
        return commits

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

    try:
        update_touched_together_gen.send(None)
    except StopIteration:
        pass

    # Ensure we can still read the DB after closing.
    assert test_scheduling.get_touched_together("dom", "layout/tests") == 1

    update_touched_together_gen = test_scheduling.update_touched_together()
    next(update_touched_together_gen)

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

    commits = [
        repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=("reviewer1",),
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
        repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer2",),
        ).set_files(["layout/file.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit4",
            author="author1",
            desc="commit4",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
    ]
    commits = [c.to_dict() for c in commits]

    def mock_get_commits():
        return commits

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


def test_touched_together_with_backout(monkeypatch):
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
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "dom/tests/manifest1.ini"], {}),
        repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=("reviewer1",),
        ).set_files(["dom/file2.cpp", "layout/tests/manifest2.ini"], {}),
    ]
    commits = [c.to_dict() for c in commits]

    def mock_get_commits():
        return commits

    monkeypatch.setattr(repository, "get_commits", mock_get_commits)

    update_touched_together_gen = test_scheduling.update_touched_together()
    next(update_touched_together_gen)

    update_touched_together_gen.send("commitbackedout")

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


@pytest.mark.parametrize("granularity", ["group", "label"])
def test_generate_data(granularity):
    past_failures = test_scheduling.get_past_failures(granularity)

    commits = [
        {
            "types": ["C/C++"],
            "files": ["dom/file1.cpp"],
            "directories": ["dom"],
            "components": ["DOM"],
        },
        {
            "types": ["C/C++"],
            "files": ["dom/file1.cpp", "dom/file2.cpp"],
            "directories": ["dom"],
            "components": ["DOM"],
        },
        {
            "types": ["C/C++"],
            "files": ["layout/file.cpp"],
            "directories": ["layout"],
            "components": ["Layout"],
        },
        {
            "types": ["C/C++"],
            "files": ["layout/file.cpp"],
            "directories": ["layout"],
            "components": ["Layout"],
        },
        {
            "types": ["JavaScript", "C/C++"],
            "files": ["dom/file1.cpp", "dom/file1.js"],
            "directories": ["dom"],
            "components": ["DOM"],
        },
    ]

    data = list(
        test_scheduling.generate_data(
            past_failures, commits[0], 1, ["runnable1", "runnable2"], [], []
        )
    )
    assert len(data) == 2
    assert data[0] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }
    assert data[1] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }

    data = list(
        test_scheduling.generate_data(
            past_failures, commits[1], 2, ["runnable1", "runnable2"], ["runnable1"], []
        )
    )
    assert len(data) == 2
    assert data[0] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }
    assert data[1] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }

    data = list(
        test_scheduling.generate_data(
            past_failures, commits[2], 3, ["runnable1", "runnable2"], [], ["runnable2"]
        )
    )
    assert len(data) == 2
    assert data[0] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }
    assert data[1] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }

    data = list(
        test_scheduling.generate_data(
            past_failures, commits[3], 4, ["runnable1"], [], []
        )
    )
    assert len(data) == 1
    assert data[0] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }

    data = list(
        test_scheduling.generate_data(
            past_failures,
            commits[4],
            1500,
            ["runnable1", "runnable2"],
            ["runnable1", "runnable2"],
            [],
        )
    )
    assert len(data) == 2
    assert data[0] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }
    assert data[1] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }

    data = list(
        test_scheduling.generate_data(
            past_failures,
            commits[4],
            2400,
            ["runnable1", "runnable2"],
            ["runnable1", "runnable2"],
            [],
        )
    )
    assert len(data) == 2
    assert data[0] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }
    assert data[1] == {
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
        "touched_together_directories": 0,
        "touched_together_files": 0,
    }
