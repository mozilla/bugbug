# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import shutil
from datetime import datetime

import hglib
import pytest
import responses

from bugbug import repository


@pytest.fixture
def fake_hg_repo(tmpdir):
    tmp_path = tmpdir.strpath
    dest = os.path.join(tmp_path, "repos")
    local = os.path.join(dest, "local")
    remote = os.path.join(dest, "remote")
    for d in [local, remote]:
        os.makedirs(d)
        hglib.init(d)

    os.environ["USER"] = "app"
    hg = hglib.open(local)

    hg.branch(b"central")

    responses.add_passthru("http://localhost:8000")

    yield hg, local, remote

    hg.close()


def copy_pushlog_database(remote, local):
    shutil.copyfile(
        os.path.join(remote, ".hg/pushlog2.db"), os.path.join(local, ".hg/pushlog2.db")
    )


def add_file(hg, repo_dir, name, contents):
    path = os.path.join(repo_dir, name)

    with open(path, "w") as f:
        f.write(contents)

    hg.add(files=[bytes(path, "ascii")])


def commit(hg):
    commit_message = "Commit {}".format(hg.status())

    i, revision = hg.commit(message=commit_message, user="Moz Illa <milla@mozilla.org>")

    return str(revision, "ascii")


def test_get_revs(fake_hg_repo):
    hg, local, remote = fake_hg_repo

    add_file(hg, local, "file1", "1\n2\n3\n4\n5\n6\n7\n")
    revision1 = commit(hg)

    revs = repository.get_revs(hg)

    assert len(revs) == 1, "There should be one revision now"
    assert revs[0].decode("ascii") == revision1

    add_file(hg, local, "file2", "1\n2\n3\n4\n5\n6\n7\n")
    revision2 = commit(hg)

    revs = repository.get_revs(hg)

    assert len(revs) == 2, "There should be two revisions now"
    assert revs[0].decode("ascii") == revision1
    assert revs[1].decode("ascii") == revision2


def test_get_directories():
    assert repository.get_directories("") == []
    assert repository.get_directories("Makefile") == []
    assert repository.get_directories("dom/aFile.jsm") == ["dom"]
    assert set(
        repository.get_directories("tools/code-coverage/CodeCoverageHandler.cpp")
    ) == {"tools", "tools/code-coverage"}

    assert repository.get_directories([""]) == []
    assert repository.get_directories(["Makefile"]) == []
    assert repository.get_directories(["dom/aFile.jsm"]) == ["dom"]
    assert set(
        repository.get_directories(["tools/code-coverage/CodeCoverageHandler.cpp"])
    ) == {"tools", "tools/code-coverage"}
    assert set(
        repository.get_directories(
            ["dom/aFile.jsm", "tools/code-coverage/CodeCoverageHandler.cpp"]
        )
    ) == {"dom", "tools", "tools/code-coverage"}


# Testing the get commits method
def test_get_commits():
    retrieved_commits = repository.get_commits()

    # Checking if were getting the same number of commits as in JSON
    assert len(list(retrieved_commits)) == 5


def test_calculate_experiences():
    commits = [
        repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug="123",
            backedoutby="",
            author_email="author1@mozilla.org",
            files=["dom/file1.cpp", "apps/file1.jsm"],
            file_copies={},
            reviewers=("reviewer1", "reviewer2"),
        ),
        repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug="123",
            backedoutby="",
            author_email="author2@mozilla.org",
            files=["dom/file1.cpp"],
            file_copies={},
            reviewers=("reviewer1",),
        ),
        repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug="123",
            backedoutby="",
            author_email="author1@mozilla.org",
            files=["dom/file2.cpp", "apps/file1.jsm"],
            file_copies={},
            reviewers=("reviewer2",),
        ),
        repository.Commit(
            node="commit4",
            author="author2",
            desc="commit4",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2020, 1, 1),
            bug="123",
            backedoutby="",
            author_email="author2@mozilla.org",
            files=["dom/file1.cpp", "apps/file2.jsm"],
            file_copies={},
            reviewers=("reviewer1", "reviewer2"),
        ),
        repository.Commit(
            node="commit5",
            author="author3",
            desc="commit5",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2020, 1, 2),
            bug="123",
            backedoutby="",
            author_email="author3@mozilla.org",
            files=["dom/file1.cpp"],
            file_copies={"dom/file1.cpp": "dom/file1copied.cpp"},
            reviewers=("reviewer3",),
        ),
        repository.Commit(
            node="commit6",
            author="author3",
            desc="commit6",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2020, 1, 3),
            bug="123",
            backedoutby="",
            author_email="author3@mozilla.org",
            files=["dom/file1.cpp", "dom/file1copied.cpp"],
            file_copies={},
            reviewers=("reviewer3",),
        ),
    ]

    repository.path_to_component = {
        "dom/file1.cpp": "Core::DOM",
        "dom/file1copied.cpp": "Core::DOM",
        "dom/file2.cpp": "Core::Layout",
        "apps/file1.jsm": "Firefox::Boh",
        "apps/file2.jsm": "Firefox::Boh",
    }

    repository.calculate_experiences(commits)

    assert repository.experiences_by_commit["total"]["author"]["commit1"] == 0
    assert repository.experiences_by_commit["total"]["author"]["commit2"] == 0
    assert repository.experiences_by_commit["total"]["author"]["commit3"] == 1
    assert repository.experiences_by_commit["total"]["author"]["commit4"] == 1
    assert repository.experiences_by_commit["total"]["author"]["commit5"] == 0
    assert repository.experiences_by_commit["total"]["author"]["commit6"] == 1

    assert repository.experiences_by_commit["90_days"]["author"]["commit1"] == 0
    assert repository.experiences_by_commit["90_days"]["author"]["commit2"] == 0
    assert repository.experiences_by_commit["90_days"]["author"]["commit3"] == 1
    assert repository.experiences_by_commit["90_days"]["author"]["commit4"] == 0
    assert repository.experiences_by_commit["90_days"]["author"]["commit5"] == 0
    assert repository.experiences_by_commit["90_days"]["author"]["commit6"] == 1

    assert repository.experiences_by_commit["total"]["reviewer"]["commit1"] == 0
    assert repository.experiences_by_commit["total"]["reviewer"]["commit2"] == 1
    assert repository.experiences_by_commit["total"]["reviewer"]["commit3"] == 1
    assert repository.experiences_by_commit["total"]["reviewer"]["commit4"] == 4
    assert repository.experiences_by_commit["total"]["reviewer"]["commit5"] == 0
    assert repository.experiences_by_commit["total"]["reviewer"]["commit6"] == 1

    assert repository.experiences_by_commit["90_days"]["reviewer"]["commit1"] == 0
    assert repository.experiences_by_commit["90_days"]["reviewer"]["commit2"] == 1
    assert repository.experiences_by_commit["90_days"]["reviewer"]["commit3"] == 1
    assert repository.experiences_by_commit["90_days"]["reviewer"]["commit4"] == 0
    assert repository.experiences_by_commit["90_days"]["reviewer"]["commit5"] == 0
    assert repository.experiences_by_commit["90_days"]["reviewer"]["commit6"] == 1

    assert repository.experiences_by_commit["total"]["file"]["commit1"] == 0
    assert repository.experiences_by_commit["total"]["file"]["commit2"] == 1
    assert repository.experiences_by_commit["total"]["file"]["commit3"] == 1
    assert repository.experiences_by_commit["total"]["file"]["commit4"] == 2
    assert repository.experiences_by_commit["total"]["file"]["commit5"] == 3
    assert repository.experiences_by_commit["total"]["file"]["commit6"] == 4

    assert repository.experiences_by_commit["90_days"]["file"]["commit1"] == 0
    assert repository.experiences_by_commit["90_days"]["file"]["commit2"] == 1
    assert repository.experiences_by_commit["90_days"]["file"]["commit3"] == 1
    assert repository.experiences_by_commit["90_days"]["file"]["commit4"] == 0
    assert repository.experiences_by_commit["90_days"]["file"]["commit5"] == 1
    assert repository.experiences_by_commit["90_days"]["file"]["commit6"] == 2

    assert repository.experiences_by_commit["total"]["directory"]["commit1"] == 0
    assert repository.experiences_by_commit["total"]["directory"]["commit2"] == 1
    assert repository.experiences_by_commit["total"]["directory"]["commit3"] == 2
    assert repository.experiences_by_commit["total"]["directory"]["commit4"] == 3
    assert repository.experiences_by_commit["total"]["directory"]["commit5"] == 4
    assert repository.experiences_by_commit["total"]["directory"]["commit6"] == 5

    assert repository.experiences_by_commit["90_days"]["directory"]["commit1"] == 0
    assert repository.experiences_by_commit["90_days"]["directory"]["commit2"] == 1
    assert repository.experiences_by_commit["90_days"]["directory"]["commit3"] == 2
    assert repository.experiences_by_commit["90_days"]["directory"]["commit4"] == 0
    assert repository.experiences_by_commit["90_days"]["directory"]["commit5"] == 1
    assert repository.experiences_by_commit["90_days"]["directory"]["commit6"] == 2

    assert repository.experiences_by_commit["total"]["component"]["commit1"] == 0
    assert repository.experiences_by_commit["total"]["component"]["commit2"] == 1
    assert repository.experiences_by_commit["total"]["component"]["commit3"] == 1
    assert repository.experiences_by_commit["total"]["component"]["commit4"] == 3
    assert repository.experiences_by_commit["total"]["component"]["commit5"] == 3
    assert repository.experiences_by_commit["total"]["component"]["commit6"] == 4

    assert repository.experiences_by_commit["90_days"]["component"]["commit1"] == 0
    assert repository.experiences_by_commit["90_days"]["component"]["commit2"] == 1
    assert repository.experiences_by_commit["90_days"]["component"]["commit3"] == 1
    assert repository.experiences_by_commit["90_days"]["component"]["commit4"] == 0
    assert repository.experiences_by_commit["90_days"]["component"]["commit5"] == 1
    assert repository.experiences_by_commit["90_days"]["component"]["commit6"] == 2
