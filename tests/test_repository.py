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


def test_exp_queue():
    q = repository.exp_queue(0, 4, 0)
    q[0] = 1
    assert q[0] == 1
    q[0] = 2
    assert q[0] == 2

    q = repository.exp_queue(366, 91, 0)
    assert q[366] == 0
    assert q[276] == 0
    q[366] += 1
    assert q[367] == 1
    assert q[277] == 0

    q = repository.exp_queue(0, 4, 0)
    assert q[0] == 0
    q[0] += 1
    assert q[0] == 1
    q[0] += 1
    assert q[0] == 2
    assert q[1] == 2
    q[1] += 1
    assert q[1] == 3
    assert q[9] == 3
    q[9] += 1
    assert q[9] == 4
    assert q[6] == 3
    assert q[11] == 4
    q[11] += 1
    assert q[11] == 5
    q[12] += 1
    assert q[12] == 6
    q[13] += 1
    assert q[13] == 7
    q[14] += 1
    assert q[14] == 8
    q[15] += 1
    assert q[15] == 9

    q = repository.exp_queue(0, 4, 0)
    assert q[0] == 0
    q[0] += 1
    assert q[0] == 1
    assert q[1] == 1
    assert q[9] == 1
    q[9] += 1
    assert q[9] == 2
    assert q[10] == 2
    assert q[8] == 1
    assert q[7] == 1
    assert q[6] == 1

    q = repository.exp_queue(9, 3, 0)
    assert q[8] == 0
    assert q[9] == 0
    q[9] += 1
    assert q[11] == 1
    assert q[10] == 1
    assert q[8] == 0
    assert q[11] == 1
    assert q[8] == 0
    assert q[9] == 1
    assert q[12] == 1


def test_calculate_experiences():
    commits = {
        "commit1": repository.Commit(
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
        "commitbackedout": repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug="123",
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            files=["dom/file1.cpp", "apps/file1.jsm"],
            file_copies={},
            reviewers=("reviewer1", "reviewer2"),
        ),
        "commit2": repository.Commit(
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
        "commit3": repository.Commit(
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
        "commit4": repository.Commit(
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
        "commit5": repository.Commit(
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
        "commit6": repository.Commit(
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
    }

    repository.path_to_component = {
        "dom/file1.cpp": "Core::DOM",
        "dom/file1copied.cpp": "Core::DOM",
        "dom/file2.cpp": "Core::Layout",
        "apps/file1.jsm": "Firefox::Boh",
        "apps/file2.jsm": "Firefox::Boh",
    }

    repository.calculate_experiences(list(commits.values()))

    assert commits["commit1"].seniority_author == 0
    assert commits["commitbackedout"].seniority_author == 0
    assert commits["commit2"].seniority_author == 0
    assert commits["commit3"].seniority_author == 0
    assert commits["commit4"].seniority_author == 365
    assert commits["commit5"].seniority_author == 0
    assert commits["commit6"].seniority_author == 1

    assert commits["commit1"].touched_prev_total_author_sum == 0
    assert commits["commit2"].touched_prev_total_author_sum == 0
    assert commits["commit3"].touched_prev_total_author_sum == 1
    assert commits["commit4"].touched_prev_total_author_sum == 1
    assert commits["commit5"].touched_prev_total_author_sum == 0
    assert commits["commit6"].touched_prev_total_author_sum == 1

    assert commits["commit1"].touched_prev_90_days_author_sum == 0
    assert commits["commit2"].touched_prev_90_days_author_sum == 0
    assert commits["commit3"].touched_prev_90_days_author_sum == 1
    assert commits["commit4"].touched_prev_90_days_author_sum == 0
    assert commits["commit5"].touched_prev_90_days_author_sum == 0
    assert commits["commit6"].touched_prev_90_days_author_sum == 1

    assert commits["commit1"].touched_prev_total_reviewer_sum == 0
    assert commits["commit1"].touched_prev_total_reviewer_max == 0
    assert commits["commit1"].touched_prev_total_reviewer_min == 0
    assert commits["commit2"].touched_prev_total_reviewer_sum == 1
    assert commits["commit2"].touched_prev_total_reviewer_max == 1
    assert commits["commit2"].touched_prev_total_reviewer_min == 1
    assert commits["commit3"].touched_prev_total_reviewer_sum == 1
    assert commits["commit3"].touched_prev_total_reviewer_max == 1
    assert commits["commit3"].touched_prev_total_reviewer_min == 1
    assert commits["commit4"].touched_prev_total_reviewer_sum == 4
    assert commits["commit4"].touched_prev_total_reviewer_max == 2
    assert commits["commit4"].touched_prev_total_reviewer_min == 2
    assert commits["commit5"].touched_prev_total_reviewer_sum == 0
    assert commits["commit5"].touched_prev_total_reviewer_max == 0
    assert commits["commit5"].touched_prev_total_reviewer_min == 0
    assert commits["commit6"].touched_prev_total_reviewer_sum == 1
    assert commits["commit6"].touched_prev_total_reviewer_max == 1
    assert commits["commit6"].touched_prev_total_reviewer_min == 1

    assert commits["commit1"].touched_prev_90_days_reviewer_sum == 0
    assert commits["commit1"].touched_prev_90_days_reviewer_max == 0
    assert commits["commit1"].touched_prev_90_days_reviewer_min == 0
    assert commits["commit2"].touched_prev_90_days_reviewer_sum == 1
    assert commits["commit2"].touched_prev_90_days_reviewer_max == 1
    assert commits["commit2"].touched_prev_90_days_reviewer_min == 1
    assert commits["commit3"].touched_prev_90_days_reviewer_sum == 1
    assert commits["commit3"].touched_prev_90_days_reviewer_max == 1
    assert commits["commit3"].touched_prev_90_days_reviewer_min == 1
    assert commits["commit4"].touched_prev_90_days_reviewer_sum == 0
    assert commits["commit4"].touched_prev_90_days_reviewer_max == 0
    assert commits["commit4"].touched_prev_90_days_reviewer_min == 0
    assert commits["commit5"].touched_prev_90_days_reviewer_sum == 0
    assert commits["commit5"].touched_prev_90_days_reviewer_max == 0
    assert commits["commit5"].touched_prev_90_days_reviewer_min == 0
    assert commits["commit6"].touched_prev_90_days_reviewer_sum == 1
    assert commits["commit6"].touched_prev_90_days_reviewer_max == 1
    assert commits["commit6"].touched_prev_90_days_reviewer_min == 1

    assert commits["commit1"].touched_prev_total_file_sum == 0
    assert commits["commit1"].touched_prev_total_file_max == 0
    assert commits["commit1"].touched_prev_total_file_min == 0
    assert commits["commit2"].touched_prev_total_file_sum == 1
    assert commits["commit2"].touched_prev_total_file_max == 1
    assert commits["commit2"].touched_prev_total_file_min == 1
    assert commits["commit3"].touched_prev_total_file_sum == 1
    assert commits["commit3"].touched_prev_total_file_max == 1
    assert commits["commit3"].touched_prev_total_file_min == 0
    assert commits["commit4"].touched_prev_total_file_sum == 2
    assert commits["commit4"].touched_prev_total_file_max == 2
    assert commits["commit4"].touched_prev_total_file_min == 0
    assert commits["commit5"].touched_prev_total_file_sum == 3
    assert commits["commit5"].touched_prev_total_file_max == 3
    assert commits["commit5"].touched_prev_total_file_min == 3
    assert commits["commit6"].touched_prev_total_file_sum == 4
    assert commits["commit6"].touched_prev_total_file_max == 4
    assert commits["commit6"].touched_prev_total_file_min == 3

    assert commits["commit1"].touched_prev_90_days_file_sum == 0
    assert commits["commit1"].touched_prev_90_days_file_max == 0
    assert commits["commit1"].touched_prev_90_days_file_min == 0
    assert commits["commit2"].touched_prev_90_days_file_sum == 1
    assert commits["commit2"].touched_prev_90_days_file_max == 1
    assert commits["commit2"].touched_prev_90_days_file_min == 1
    assert commits["commit3"].touched_prev_90_days_file_sum == 1
    assert commits["commit3"].touched_prev_90_days_file_max == 1
    assert commits["commit3"].touched_prev_90_days_file_min == 0
    assert commits["commit4"].touched_prev_90_days_file_sum == 0
    assert commits["commit4"].touched_prev_90_days_file_max == 0
    assert commits["commit4"].touched_prev_90_days_file_min == 0
    assert commits["commit5"].touched_prev_90_days_file_sum == 1
    assert commits["commit5"].touched_prev_90_days_file_max == 1
    assert commits["commit5"].touched_prev_90_days_file_min == 1
    assert commits["commit6"].touched_prev_90_days_file_sum == 2
    assert commits["commit6"].touched_prev_90_days_file_max == 2
    assert commits["commit6"].touched_prev_90_days_file_min == 1

    assert commits["commit1"].touched_prev_total_directory_sum == 0
    assert commits["commit1"].touched_prev_total_directory_max == 0
    assert commits["commit1"].touched_prev_total_directory_min == 0
    assert commits["commit2"].touched_prev_total_directory_sum == 1
    assert commits["commit2"].touched_prev_total_directory_max == 1
    assert commits["commit2"].touched_prev_total_directory_min == 1
    assert commits["commit3"].touched_prev_total_directory_sum == 2
    assert commits["commit3"].touched_prev_total_directory_max == 2
    assert commits["commit3"].touched_prev_total_directory_min == 1
    assert commits["commit4"].touched_prev_total_directory_sum == 3
    assert commits["commit4"].touched_prev_total_directory_max == 3
    assert commits["commit4"].touched_prev_total_directory_min == 2
    assert commits["commit5"].touched_prev_total_directory_sum == 4
    assert commits["commit5"].touched_prev_total_directory_max == 4
    assert commits["commit5"].touched_prev_total_directory_min == 4
    assert commits["commit6"].touched_prev_total_directory_sum == 5
    assert commits["commit6"].touched_prev_total_directory_max == 5
    assert commits["commit6"].touched_prev_total_directory_min == 5

    assert commits["commit1"].touched_prev_90_days_directory_sum == 0
    assert commits["commit1"].touched_prev_90_days_directory_max == 0
    assert commits["commit1"].touched_prev_90_days_directory_min == 0
    assert commits["commit2"].touched_prev_90_days_directory_sum == 1
    assert commits["commit2"].touched_prev_90_days_directory_max == 1
    assert commits["commit2"].touched_prev_90_days_directory_min == 1
    assert commits["commit3"].touched_prev_90_days_directory_sum == 2
    assert commits["commit3"].touched_prev_90_days_directory_max == 2
    assert commits["commit3"].touched_prev_90_days_directory_min == 1
    assert commits["commit4"].touched_prev_90_days_directory_sum == 0
    assert commits["commit4"].touched_prev_90_days_directory_max == 0
    assert commits["commit4"].touched_prev_90_days_directory_min == 0
    assert commits["commit5"].touched_prev_90_days_directory_sum == 1
    assert commits["commit5"].touched_prev_90_days_directory_max == 1
    assert commits["commit5"].touched_prev_90_days_directory_min == 1
    assert commits["commit6"].touched_prev_90_days_directory_sum == 2
    assert commits["commit6"].touched_prev_90_days_directory_max == 2
    assert commits["commit6"].touched_prev_90_days_directory_min == 2

    assert commits["commit1"].touched_prev_total_component_sum == 0
    assert commits["commit1"].touched_prev_total_component_max == 0
    assert commits["commit1"].touched_prev_total_component_min == 0
    assert commits["commit2"].touched_prev_total_component_sum == 1
    assert commits["commit2"].touched_prev_total_component_max == 1
    assert commits["commit2"].touched_prev_total_component_min == 1
    assert commits["commit3"].touched_prev_total_component_sum == 1
    assert commits["commit3"].touched_prev_total_component_max == 1
    assert commits["commit3"].touched_prev_total_component_min == 0
    assert commits["commit4"].touched_prev_total_component_sum == 3
    assert commits["commit4"].touched_prev_total_component_max == 2
    assert commits["commit4"].touched_prev_total_component_min == 2
    assert commits["commit5"].touched_prev_total_component_sum == 3
    assert commits["commit5"].touched_prev_total_component_max == 3
    assert commits["commit5"].touched_prev_total_component_min == 3
    assert commits["commit6"].touched_prev_total_component_sum == 4
    assert commits["commit6"].touched_prev_total_component_max == 4
    assert commits["commit6"].touched_prev_total_component_min == 4

    assert commits["commit1"].touched_prev_90_days_component_sum == 0
    assert commits["commit1"].touched_prev_90_days_component_max == 0
    assert commits["commit1"].touched_prev_90_days_component_min == 0
    assert commits["commit2"].touched_prev_90_days_component_sum == 1
    assert commits["commit2"].touched_prev_90_days_component_max == 1
    assert commits["commit2"].touched_prev_90_days_component_min == 1
    assert commits["commit3"].touched_prev_90_days_component_sum == 1
    assert commits["commit3"].touched_prev_90_days_component_max == 1
    assert commits["commit3"].touched_prev_90_days_component_min == 0
    assert commits["commit4"].touched_prev_90_days_component_sum == 0
    assert commits["commit4"].touched_prev_90_days_component_max == 0
    assert commits["commit4"].touched_prev_90_days_component_min == 0
    assert commits["commit5"].touched_prev_90_days_component_sum == 1
    assert commits["commit5"].touched_prev_90_days_component_max == 1
    assert commits["commit5"].touched_prev_90_days_component_min == 1
    assert commits["commit6"].touched_prev_90_days_component_sum == 2
    assert commits["commit6"].touched_prev_90_days_component_max == 2
    assert commits["commit6"].touched_prev_90_days_component_min == 2
