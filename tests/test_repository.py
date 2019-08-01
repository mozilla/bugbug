# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import shutil
import time
from datetime import datetime, timezone

import hglib
import pytest
import responses
from dateutil.relativedelta import relativedelta

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


def commit(hg, commit_message=None, date=datetime(2019, 4, 16, tzinfo=timezone.utc)):
    commit_message = (
        commit_message
        if commit_message is not None
        else "Commit {}".format(
            " ".join(
                [elem.decode("ascii") for status in hg.status() for elem in status]
            )
        )
    )

    i, revision = hg.commit(
        message=commit_message, user="Moz Illa <milla@mozilla.org>", date=date
    )

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

    add_file(hg, local, "file3", "1\n2\n3\n4\n5\n6\n7\n")
    revision3 = commit(hg)

    revs = repository.get_revs(hg)

    assert len(revs) == 3, "There should be three revisions now"
    assert revs[0].decode("ascii") == revision1
    assert revs[1].decode("ascii") == revision2
    assert revs[2].decode("ascii") == revision3

    revs = repository.get_revs(hg, revision2)

    assert len(revs) == 2, "There should be two revisions after the first"
    assert revs[0].decode("ascii") == revision2
    assert revs[1].decode("ascii") == revision3

    add_file(hg, local, "file4", "1\n2\n3\n4\n5\n6\n7\n")
    commit(hg)

    revs = repository.get_revs(hg, revision2, revision3)

    assert (
        len(revs) == 2
    ), "There should be two revision after the first and up to the third"
    assert revs[0].decode("ascii") == revision2
    assert revs[1].decode("ascii") == revision3


def test_hg_modified_files(fake_hg_repo):
    hg, local, remote = fake_hg_repo

    add_file(hg, local, "f1", "1\n2\n3\n4\n5\n6\n7\n")
    revision1 = commit(hg, date=datetime(1991, 4, 16, tzinfo=timezone.utc))

    add_file(hg, local, "f2", "1\n2\n3\n4\n5\n6\n7\n")
    revision2 = commit(hg, "Bug 123 - Prova. r=moz,rev2")

    hg.copy(
        bytes(os.path.join(local, "f2"), "ascii"),
        bytes(os.path.join(local, "f2copy"), "ascii"),
    )
    revision3 = commit(hg, "Copy")

    hg.move(
        bytes(os.path.join(local, "f2copy"), "ascii"),
        bytes(os.path.join(local, "f2copymove"), "ascii"),
    )
    revision4 = commit(hg, "Move")

    hg.push(dest=bytes(remote, "ascii"))
    revs = repository.get_revs(hg, revision1)
    commits = repository.hg_log(hg, revs)

    for c in commits:
        repository.hg_modified_files(hg, c)

    assert commits[0].node == revision1
    assert commits[0].files == ["f1"]
    assert commits[0].file_copies == {}

    assert commits[1].node == revision2
    assert commits[1].files == ["f2"]
    assert commits[1].file_copies == {}

    assert commits[2].node == revision3
    assert commits[2].files == ["f2copy"]
    assert commits[2].file_copies == {"f2": "f2copy"}

    assert commits[3].node == revision4
    assert commits[3].files == ["f2copy", "f2copymove"]
    assert commits[3].file_copies == {"f2copy": "f2copymove"}


def test_hg_log(fake_hg_repo):
    hg, local, remote = fake_hg_repo

    add_file(hg, local, "file1", "1\n2\n3\n4\n5\n6\n7\n")
    revision1 = commit(hg, date=datetime(1991, 4, 16, tzinfo=timezone.utc))

    first_push_date = datetime.utcnow()
    hg.push(dest=bytes(remote, "ascii"))

    add_file(hg, local, "file2", "1\n2\n3\n4\n5\n6\n7\n")
    revision2 = commit(hg, "Bug 123 - Prova. r=moz,rev2")

    hg.copy(
        bytes(os.path.join(local, "file2"), "ascii"),
        bytes(os.path.join(local, "file2copy"), "ascii"),
    )
    revision3 = commit(hg)

    hg.move(
        bytes(os.path.join(local, "file2copy"), "ascii"),
        bytes(os.path.join(local, "file2copymove"), "ascii"),
    )
    revision4 = commit(hg)

    hg.backout(
        rev=revision4,
        message=f"Backout {revision4[:12]}",
        user="sheriff",
        date=datetime(2019, 4, 16, tzinfo=timezone.utc),
    )
    revision5 = hg.log(limit=1)[0][1].decode("ascii")

    # Wait one second, to have a different pushdate.
    time.sleep(1)

    second_push_date = datetime.utcnow()
    hg.push(dest=bytes(remote, "ascii"))

    add_file(hg, local, "file3", "1\n2\n3\n4\n5\n6\n7\n")
    revision6 = commit(hg)

    copy_pushlog_database(remote, local)

    revs = repository.get_revs(hg)

    # Wait one second, to have a different pushdate.
    time.sleep(1)

    hg_log_date = datetime.utcnow()

    commits = repository.hg_log(hg, revs)
    assert len(commits) == 6, "hg log should return six commits"

    assert commits[0].node == revision1
    assert commits[0].author == "Moz Illa <milla@mozilla.org>"
    assert commits[0].desc == "Commit A file1"
    assert commits[0].date == datetime(1991, 4, 16)
    assert (
        first_push_date - relativedelta(seconds=1)
        <= commits[0].pushdate
        <= first_push_date + relativedelta(seconds=1)
    )
    assert commits[0].bug_id is None
    assert commits[0].backedoutby == ""
    assert commits[0].author_email == "milla@mozilla.org"
    assert commits[0].reviewers == tuple()

    assert commits[1].node == revision2
    assert commits[1].author == "Moz Illa <milla@mozilla.org>"
    assert commits[1].desc == "Bug 123 - Prova. r=moz,rev2"
    assert commits[1].date == datetime(2019, 4, 16)
    assert (
        second_push_date - relativedelta(seconds=1)
        <= commits[1].pushdate
        <= second_push_date + relativedelta(seconds=1)
    )
    assert commits[1].bug_id == 123
    assert commits[1].backedoutby == ""
    assert commits[1].author_email == "milla@mozilla.org"
    assert set(commits[1].reviewers) == {"moz", "rev2"}

    assert commits[2].node == revision3
    assert commits[2].author == "Moz Illa <milla@mozilla.org>"
    assert commits[2].desc == "Commit A file2copy"
    assert commits[2].date == datetime(2019, 4, 16)
    assert (
        second_push_date - relativedelta(seconds=1)
        <= commits[2].pushdate
        <= second_push_date + relativedelta(seconds=1)
    )
    assert commits[2].bug_id is None
    assert commits[2].backedoutby == ""
    assert commits[2].author_email == "milla@mozilla.org"
    assert commits[2].reviewers == tuple()

    assert commits[3].node == revision4
    assert commits[3].author == "Moz Illa <milla@mozilla.org>"
    assert commits[3].desc == "Commit A file2copymove R file2copy"
    assert commits[3].date == datetime(2019, 4, 16)
    assert (
        second_push_date - relativedelta(seconds=1)
        <= commits[3].pushdate
        <= second_push_date + relativedelta(seconds=1)
    )
    assert commits[3].bug_id is None
    assert commits[3].backedoutby == revision5
    assert commits[3].author_email == "milla@mozilla.org"
    assert commits[3].reviewers == tuple()

    assert commits[4].node == revision5
    assert commits[4].author == "sheriff"
    assert commits[4].desc == f"Backout {revision4[:12]}"
    assert commits[4].date == datetime(2019, 4, 16)
    assert (
        second_push_date - relativedelta(seconds=1)
        <= commits[4].pushdate
        <= second_push_date + relativedelta(seconds=1)
    )
    assert commits[4].bug_id is None
    assert commits[4].backedoutby == ""
    assert commits[4].author_email == "sheriff"
    assert commits[4].reviewers == tuple()

    assert commits[5].node == revision6
    assert commits[5].author == "Moz Illa <milla@mozilla.org>"
    assert commits[5].desc == "Commit A file3"
    assert commits[5].date == datetime(2019, 4, 16)
    assert (
        hg_log_date - relativedelta(seconds=1)
        <= commits[5].pushdate
        <= hg_log_date + relativedelta(seconds=1)
    )
    assert commits[5].bug_id is None
    assert commits[5].backedoutby == ""
    assert commits[5].author_email == "milla@mozilla.org"
    assert commits[5].reviewers == tuple()

    commits = repository.hg_log(hg, [revs[1], revs[3]])
    assert len(commits) == 3, "hg log should return three commits"
    assert commits[0].node == revision2
    assert commits[1].node == revision3
    assert commits[2].node == revision4


@responses.activate
def test_download_component_mapping():
    responses.add(
        responses.HEAD,
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        headers={"ETag": "100"},
    )

    responses.add(
        responses.GET,
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        json={},
    )

    repository.download_component_mapping()
    assert len(repository.path_to_component) == 0

    responses.reset()
    responses.add(
        responses.HEAD,
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        headers={"ETag": "101"},
    )

    responses.add(
        responses.GET,
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        json={
            "AUTHORS": ["mozilla.org", "Licensing"],
            "Cargo.lock": ["Firefox Build System", "General"],
        },
    )

    repository.download_component_mapping()
    assert len(repository.path_to_component) == 2
    assert repository.path_to_component["AUTHORS"] == "mozilla.org::Licensing"
    assert repository.path_to_component["Cargo.lock"] == "Firefox Build System::General"

    responses.reset()
    responses.add(
        responses.HEAD,
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        headers={"ETag": "101"},
    )

    repository.download_component_mapping()
    assert len(repository.path_to_component) == 2
    assert repository.path_to_component["AUTHORS"] == "mozilla.org::Licensing"
    assert repository.path_to_component["Cargo.lock"] == "Firefox Build System::General"


@responses.activate
def test_download_commits(fake_hg_repo):
    hg, local, remote = fake_hg_repo

    responses.add(
        responses.HEAD,
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        headers={"ETag": "123"},
    )

    responses.add(
        responses.GET,
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        json={
            "file1": ["Firefox", "Menus"],
            "file2": ["Firefox", "General"],
            "file3": ["Core", "General"],
        },
    )

    # Remove the mock DB generated by the mock_data fixture.
    os.remove("data/commits.json")

    with open(os.path.join(local, ".hg-annotate-ignore-revs"), "w") as f:
        f.write("not_existing_hash\n")

    add_file(hg, local, "file1", "1\n2\n3\n4\n5\n6\n7\n")
    commit(hg, date=datetime(1991, 4, 16, tzinfo=timezone.utc))
    hg.push(dest=bytes(remote, "ascii"))
    copy_pushlog_database(remote, local)

    commits = repository.download_commits(local)
    assert len(commits) == 0
    commits = list(repository.get_commits())
    assert len(commits) == 0

    # Wait one second, to have a different pushdate.
    time.sleep(1)

    add_file(hg, local, "file2", "1\n2\n3\n4\n5\n6\n7\n")
    revision2 = commit(hg, "Bug 123 - Prova. r=moz,rev2")
    hg.push(dest=bytes(remote, "ascii"))
    copy_pushlog_database(remote, local)

    commits = repository.download_commits(local)
    assert len(commits) == 1
    commits = list(repository.get_commits())
    assert len(commits) == 1
    assert commits[0]["node"] == revision2
    assert commits[0]["touched_prev_total_author_sum"] == 0
    assert commits[0]["seniority_author"] > 0

    # Wait one second, to have a different pushdate.
    time.sleep(1)

    add_file(hg, local, "file3", "1\n2\n3\n4\n5\n6\n7\n")
    revision3 = commit(hg, "Bug 456 - Prova. r=moz")
    hg.push(dest=bytes(remote, "ascii"))
    copy_pushlog_database(remote, local)

    commits = repository.download_commits(local, revision3)
    assert len(commits) == 1
    commits = list(repository.get_commits())
    assert len(commits) == 2
    assert commits[0]["node"] == revision2
    assert commits[0]["touched_prev_total_author_sum"] == 0
    assert commits[0]["seniority_author"] > 0
    assert commits[1]["node"] == revision3
    assert commits[1]["touched_prev_total_author_sum"] == 1
    assert commits[1]["seniority_author"] > commits[0]["seniority_author"]

    os.remove("data/commits.json")
    os.remove("data/commit_experiences.pickle")
    commits = repository.download_commits(local, f"children({revision2})")
    assert len(commits) == 1
    assert len(list(repository.get_commits())) == 1

    os.remove("data/commits.json")
    os.remove("data/commit_experiences.pickle")
    commits = repository.download_commits(local)
    assert len(list(repository.get_commits())) == 2


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


def test_set_commits_to_ignore(tmpdir):
    tmp_path = tmpdir.strpath

    with open(os.path.join(tmp_path, ".hg-annotate-ignore-revs"), "w") as f:
        f.write("commit1\ncommit2\n8ba995b74e18334ab3707f27e9eb8f4e37ba3d29\n")

    def create_commit(node, desc, bug_id, backedoutby):
        return repository.Commit(
            node=node,
            author="author",
            desc=desc,
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=bug_id,
            backedoutby=backedoutby,
            author_email="author@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp"], {})

    commits = [
        create_commit("commit", "", 123, ""),
        create_commit("commit_backout", "", 123, ""),
        create_commit("commit_backedout", "", 123, "commit_backout"),
        create_commit("commit_no_bug", "", None, ""),
        create_commit(
            "8ba995b74e18334ab3707f27e9eb8f4e37ba3d29",
            "commit in .hg-annotate-ignore-revs",
            123,
            "",
        ),
        create_commit(
            "commit_with_ignore_in_desc", "prova\nignore-this-changeset\n", 123, ""
        ),
    ]

    repository.set_commits_to_ignore(tmp_path, commits)
    leftovers = [commit for commit in commits if commit.ignored]
    assert len(leftovers) == 4
    assert set(commit.node for commit in leftovers) == {
        "commit_backout",
        "commit_no_bug",
        "8ba995b74e18334ab3707f27e9eb8f4e37ba3d29",
        "commit_with_ignore_in_desc",
    }


def test_calculate_experiences():
    repository.path_to_component = {
        "dom/file1.cpp": "Core::DOM",
        "dom/file1copied.cpp": "Core::DOM",
        "dom/file2.cpp": "Core::Layout",
        "apps/file1.jsm": "Firefox::Boh",
        "apps/file2.jsm": "Firefox::Boh",
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
        ).set_files(["dom/file1.cpp", "apps/file1.jsm"], {}),
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
        ).set_files(["dom/file1.cpp", "apps/file1.jsm"], {}),
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
        ).set_files(["dom/file1.cpp"], {}),
        "commit2refactoring": repository.Commit(
            node="commit2refactoring",
            author="author2",
            desc="commit2refactoring",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=("reviewer1",),
            ignored=True,
        ).set_files(["dom/file1.cpp"], {}),
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
        ).set_files(["dom/file2.cpp", "apps/file1.jsm"], {}),
        "commit4": repository.Commit(
            node="commit4",
            author="author2",
            desc="commit4",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2020, 1, 1),
            bug_id=123,
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=("reviewer1", "reviewer2"),
        ).set_files(["dom/file1.cpp", "apps/file2.jsm"], {}),
        "commit5": repository.Commit(
            node="commit5",
            author="author3",
            desc="commit5",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2020, 1, 2),
            bug_id=123,
            backedoutby="",
            author_email="author3@mozilla.org",
            reviewers=("reviewer3",),
        ).set_files(["dom/file1.cpp"], {"dom/file1.cpp": "dom/file1copied.cpp"}),
        "commit6": repository.Commit(
            node="commit6",
            author="author3",
            desc="commit6",
            date=datetime(2019, 1, 1),
            pushdate=datetime(2020, 1, 3),
            bug_id=123,
            backedoutby="",
            author_email="author3@mozilla.org",
            reviewers=("reviewer3",),
        ).set_files(["dom/file1.cpp", "dom/file1copied.cpp"], {}),
    }

    repository.calculate_experiences(commits.values(), datetime(2019, 1, 1))

    assert commits["commit1"].seniority_author == 0
    assert commits["commitbackedout"].seniority_author == 0
    assert commits["commit2"].seniority_author == 0
    assert commits["commit3"].seniority_author == 0
    assert commits["commit4"].seniority_author == 86400.0 * 365
    assert commits["commit5"].seniority_author == 0
    assert commits["commit6"].seniority_author == 86400.0

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
