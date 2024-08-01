# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import pickle
import shutil
import time
from datetime import datetime, timezone
from logging import INFO, basicConfig, getLogger

import hglib
import pytest
import responses
import rs_parsepatch
import zstandard
from dateutil.relativedelta import relativedelta

from bugbug import commit_features, repository, rust_code_analysis_server

basicConfig(level=INFO)
logger = getLogger(__name__)


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


def remove_file(hg, repo_dir, name):
    path = os.path.join(repo_dir, name)
    hg.remove(files=[bytes(path, "ascii")])


def commit(
    hg,
    commit_message=None,
    date=datetime(2019, 4, 16, tzinfo=timezone.utc),
    amend=False,
):
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
        message=commit_message,
        user="Moz Illa <milla@mozilla.org>",
        date=date,
        amend=amend,
    )

    return str(revision, "ascii")


def test_clone(fake_hg_repo):
    hg, local, remote = fake_hg_repo

    add_file(hg, local, "file1", "1\n2\n3\n4\n5\n6\n7\n")
    commit(hg)
    hg.push(dest=bytes(remote, "ascii"))

    tmp_repo_dir = "ciao"

    repository.clone(tmp_repo_dir, url=remote)

    # Assert that we don't have the file from the remote repository, since we cloned
    # without updating the working dir.
    assert not os.path.exists(os.path.join(tmp_repo_dir, "file1"))

    # Assert we have the commit from the remote repository.
    remote_revs = repository.get_revs(hg)
    with hglib.open(tmp_repo_dir) as tmp_hg:
        assert repository.get_revs(tmp_hg) == remote_revs

    # Commit in the temporary repository.
    with hglib.open(tmp_repo_dir) as tmp_hg:
        add_file(tmp_hg, tmp_repo_dir, "file1", "1\n2\n3\n")
        commit(tmp_hg)

    # Commit in the remote repository.
    add_file(hg, local, "file1", "1\n2\n")
    commit(hg)
    hg.push(dest=bytes(remote, "ascii"))

    # Repository already exists, it will just be cleaned and pulled.
    repository.clone(tmp_repo_dir, url=remote)
    # Assert we only have the commits from the remote repository.
    remote_revs = repository.get_revs(hg)
    with hglib.open(tmp_repo_dir) as tmp_hg:
        assert repository.get_revs(tmp_hg) == remote_revs

    repository.clone(f"{tmp_repo_dir}2", url=remote, update=True)
    # Assert that we do have the file from the remote repository, since we cloned
    # and updated the working dir.
    assert os.path.exists(os.path.join(f"{tmp_repo_dir}2", "file1"))


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

    add_file(hg, local, "f3", "1\n2\n3\n4\n5\n6\n7\n")
    commit(hg, "tmp")
    remove_file(hg, local, "f3")
    revision5 = commit(hg, "Empty", amend=True)

    hg.push(dest=bytes(remote, "ascii"))
    revs = repository.get_revs(hg, revision1)
    commits = repository.hg_log(hg, revs)

    repository.path_to_component = {}

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

    assert commits[4].node == revision5
    assert commits[4].files == []
    assert commits[4].file_copies == {}


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
    assert (
        first_push_date - relativedelta(seconds=1)
        <= commits[0].pushdate
        <= first_push_date + relativedelta(seconds=1)
    )
    assert commits[0].bug_id is None
    assert commits[0].backedoutby == ""
    assert commits[0].author_email == "milla@mozilla.org"
    assert commits[0].reviewers == []

    assert commits[1].node == revision2
    assert commits[1].author == "Moz Illa <milla@mozilla.org>"
    assert commits[1].desc == "Bug 123 - Prova. r=moz,rev2"
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
    assert (
        second_push_date - relativedelta(seconds=1)
        <= commits[2].pushdate
        <= second_push_date + relativedelta(seconds=1)
    )
    assert commits[2].bug_id is None
    assert commits[2].backedoutby == ""
    assert commits[2].author_email == "milla@mozilla.org"
    assert commits[2].reviewers == []

    assert commits[3].node == revision4
    assert commits[3].author == "Moz Illa <milla@mozilla.org>"
    assert commits[3].desc == "Commit A file2copymove R file2copy"
    assert (
        second_push_date - relativedelta(seconds=1)
        <= commits[3].pushdate
        <= second_push_date + relativedelta(seconds=1)
    )
    assert commits[3].bug_id is None
    assert commits[3].backedoutby == revision5
    assert commits[3].author_email == "milla@mozilla.org"
    assert commits[3].reviewers == []

    assert commits[4].node == revision5
    assert commits[4].author == "sheriff"
    assert commits[4].desc == f"Backout {revision4[:12]}"
    assert (
        second_push_date - relativedelta(seconds=1)
        <= commits[4].pushdate
        <= second_push_date + relativedelta(seconds=1)
    )
    assert commits[4].bug_id is None
    assert commits[4].backedoutby == ""
    assert commits[4].author_email == "sheriff"
    assert commits[4].reviewers == []

    assert commits[5].node == revision6
    assert commits[5].author == "Moz Illa <milla@mozilla.org>"
    assert commits[5].desc == "Commit A file3"
    assert (
        hg_log_date - relativedelta(seconds=1)
        <= commits[5].pushdate
        <= hg_log_date + relativedelta(seconds=1)
    )
    assert commits[5].bug_id is None
    assert commits[5].backedoutby == ""
    assert commits[5].author_email == "milla@mozilla.org"
    assert commits[5].reviewers == []

    commits = repository.hg_log(hg, revs[1] + b":" + revs[3])
    assert len(commits) == 3, "hg log should return three commits"
    assert commits[0].node == revision2
    assert commits[1].node == revision3
    assert commits[2].node == revision4

    commits = repository.hg_log(hg, [revs[1], revs[2], revs[3]])
    assert len(commits) == 3, "hg log should return three commits"
    assert commits[0].node == revision2
    assert commits[1].node == revision3
    assert commits[2].node == revision4

    commits = repository.hg_log(hg, [revs[1], revs[3]])
    assert len(commits) == 2, "hg log should return two commits"
    assert commits[0].node == revision2
    assert commits[1].node == revision4

    add_file(hg, local, "file4", "1\n2\n3\n4\n5\n6\n7\n")
    revision7 = commit(hg, "Bug 123456 - Prova. r=me")
    commits = repository.hg_log(hg, [revision7])
    assert len(commits) == 1, "hg log should return one commit"
    assert commits[0].node == revision7
    assert commits[0].reviewers == []

    add_file(hg, local, "file5", "1\n2\n3\n4\n5\n6\n7\n")
    revision8 = commit(hg, "Bug 123456 - Prova. r=testonly,realreviewer")
    commits = repository.hg_log(hg, [revision8])
    assert len(commits) == 1, "hg log should return one commit"
    assert commits[0].node == revision8
    assert commits[0].reviewers == ["realreviewer"]


def test_download_coverage_mapping() -> None:
    cctx = zstandard.ZstdCompressor()

    responses.add(
        responses.HEAD,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/project.relman.code-coverage.production.cron.latest/artifacts/public/commit_coverage.json.zst",
        status=200,
        headers={"ETag": "100"},
    )

    responses.add(
        responses.GET,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/project.relman.code-coverage.production.cron.latest/artifacts/public/commit_coverage.json.zst",
        status=200,
        body=cctx.compress(json.dumps({}).encode("ascii")),
    )

    repository.download_coverage_mapping()

    responses.reset()
    responses.add(
        responses.HEAD,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/project.relman.code-coverage.production.cron.latest/artifacts/public/commit_coverage.json.zst",
        status=200,
        headers={"ETag": "101"},
    )

    responses.add(
        responses.GET,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/project.relman.code-coverage.production.cron.latest/artifacts/public/commit_coverage.json.zst",
        status=200,
        body=cctx.compress(
            json.dumps(
                {
                    "revision1": {
                        "added": 7,
                        "covered": 3,
                        "unknown": 0,
                    },
                    "revision2": None,
                }
            ).encode("ascii")
        ),
    )

    repository.download_coverage_mapping()
    commit_to_coverage = repository.get_coverage_mapping()
    assert pickle.loads(commit_to_coverage[b"revision1"]) == {
        "added": 7,
        "covered": 3,
        "unknown": 0,
    }
    assert pickle.loads(commit_to_coverage[b"revision2"]) is None

    responses.reset()
    commit_to_coverage = repository.get_coverage_mapping()
    assert pickle.loads(commit_to_coverage[b"revision1"]) == {
        "added": 7,
        "covered": 3,
        "unknown": 0,
    }
    assert pickle.loads(commit_to_coverage[b"revision2"]) is None
    repository.close_coverage_mapping()

    responses.reset()
    responses.add(
        responses.HEAD,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/project.relman.code-coverage.production.cron.latest/artifacts/public/commit_coverage.json.zst",
        status=200,
        headers={"ETag": "101"},
    )

    repository.download_coverage_mapping()
    repository.get_coverage_mapping()
    commit_to_coverage = repository.get_coverage_mapping()
    assert pickle.loads(commit_to_coverage[b"revision1"]) == {
        "added": 7,
        "covered": 3,
        "unknown": 0,
    }
    assert pickle.loads(commit_to_coverage[b"revision2"]) is None
    repository.close_coverage_mapping()


def test_download_component_mapping():
    responses.add(
        responses.HEAD,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        headers={"ETag": "100"},
    )

    responses.add(
        responses.GET,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        json={},
    )

    repository.download_component_mapping()

    responses.reset()
    responses.add(
        responses.HEAD,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        headers={"ETag": "101"},
    )

    responses.add(
        responses.GET,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        json={
            "AUTHORS": ["mozilla.org", "Licensing"],
            "Cargo.lock": ["Firefox Build System", "General"],
        },
    )

    repository.download_component_mapping()
    repository.get_component_mapping()
    assert repository.path_to_component[b"AUTHORS"] == b"mozilla.org::Licensing"
    assert (
        repository.path_to_component[b"Cargo.lock"] == b"Firefox Build System::General"
    )

    responses.reset()
    repository.get_component_mapping()
    assert repository.path_to_component[b"AUTHORS"] == b"mozilla.org::Licensing"
    assert (
        repository.path_to_component[b"Cargo.lock"] == b"Firefox Build System::General"
    )
    repository.close_component_mapping()

    responses.reset()
    responses.add(
        responses.HEAD,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        headers={"ETag": "101"},
    )

    repository.download_component_mapping()
    repository.get_component_mapping()
    assert repository.path_to_component[b"AUTHORS"] == b"mozilla.org::Licensing"
    assert (
        repository.path_to_component[b"Cargo.lock"] == b"Firefox Build System::General"
    )
    repository.close_component_mapping()


@pytest.mark.parametrize("use_single_process", [True, False])
def test_download_commits(fake_hg_repo, use_single_process):
    hg, local, remote = fake_hg_repo

    # Allow using the local code analysis server.
    responses.add_passthru("http://127.0.0.1")

    responses.add(
        responses.HEAD,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        headers={"ETag": "123"},
    )

    responses.add(
        responses.GET,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        json={
            "file1": ["Firefox", "Menus"],
            "file2": ["Firefox", "General"],
            "file3": ["Core", "General"],
        },
    )

    # Remove the mock DB generated by the mock_data fixture.
    os.remove("data/commits.json")

    add_file(hg, local, ".hg-annotate-ignore-revs", "not_existing_hash\n")

    add_file(hg, local, "file1", "1\n2\n3\n4\n5\n6\n7\n")
    revision1 = commit(hg, date=datetime(1991, 4, 16, tzinfo=timezone.utc))
    hg.push(dest=bytes(remote, "ascii"))
    copy_pushlog_database(remote, local)

    responses.add(
        responses.HEAD,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/project.relman.code-coverage.production.cron.latest/artifacts/public/commit_coverage.json.zst",
        status=200,
        headers={"ETag": "123"},
    )

    cctx = zstandard.ZstdCompressor()
    coverage_mapping = {revision1: None}

    def request_callback(request):
        return 200, {}, cctx.compress(json.dumps(coverage_mapping).encode("ascii"))

    responses.add_callback(
        responses.GET,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/project.relman.code-coverage.production.cron.latest/artifacts/public/commit_coverage.json.zst",
        callback=request_callback,
    )

    commits = repository.download_commits(
        local, rev_start=0, use_single_process=use_single_process
    )
    assert len(commits) == 0
    commits = list(repository.get_commits())
    assert len(commits) == 0

    # Wait one second, to have a different pushdate.
    time.sleep(1)

    add_file(hg, local, "file2", "1\n2\n3\n4\n5\n6\n7\n")
    revision2 = commit(hg, "Bug 123 - Prova. r=moz,rev2")
    hg.push(dest=bytes(remote, "ascii"))
    copy_pushlog_database(remote, local)

    coverage_mapping = {
        revision1: None,
        revision2: {
            "added": 7,
            "covered": 3,
            "unknown": 0,
        },
    }

    os.remove("data/coverage_mapping.json.zst.etag")

    commits = repository.download_commits(
        local, rev_start=0, use_single_process=use_single_process
    )
    assert len(commits) == 1
    commits = list(repository.get_commits())
    assert len(commits) == 1
    assert commits[0]["node"] == revision2
    assert commits[0]["touched_prev_total_author_sum"] == 0
    assert commits[0]["seniority_author"] > 0
    assert commits[0]["cov_added"] == 7
    assert commits[0]["cov_covered"] == 3
    assert commits[0]["cov_unknown"] == 0

    # Wait one second, to have a different pushdate.
    time.sleep(1)

    add_file(hg, local, "file3", "1\n2\n3\n4\n5\n6\n7\n")
    revision3 = commit(hg, "Bug 456 - Prova. r=moz")
    hg.push(dest=bytes(remote, "ascii"))
    copy_pushlog_database(remote, local)

    commits = repository.download_commits(
        local, rev_start=revision3, use_single_process=use_single_process
    )
    assert len(commits) == 1
    commits = list(repository.get_commits())
    assert len(commits) == 2
    assert commits[0]["node"] == revision2
    assert commits[0]["touched_prev_total_author_sum"] == 0
    assert commits[0]["seniority_author"] > 0
    assert commits[0]["cov_added"] == 7
    assert commits[0]["cov_covered"] == 3
    assert commits[0]["cov_unknown"] == 0
    assert commits[1]["node"] == revision3
    assert commits[1]["touched_prev_total_author_sum"] == 1
    assert commits[1]["seniority_author"] > commits[0]["seniority_author"]
    assert commits[1]["cov_added"] is None
    assert commits[1]["cov_covered"] is None
    assert commits[1]["cov_unknown"] is None

    os.remove("data/commits.json")
    shutil.rmtree("data/commit_experiences.lmdb")
    commits = repository.download_commits(
        local, rev_start=f"children({revision2})", use_single_process=use_single_process
    )
    assert len(commits) == 1
    assert len(list(repository.get_commits())) == 1

    os.remove("data/commits.json")
    shutil.rmtree("data/commit_experiences.lmdb")
    commits = repository.download_commits(
        local,
        revs=[revision2.encode("ascii"), revision3.encode("ascii")],
        use_single_process=use_single_process,
    )
    assert len(commits) == 2
    assert len(list(repository.get_commits())) == 2

    os.remove("data/commits.json")
    shutil.rmtree("data/commit_experiences.lmdb")
    commits = repository.download_commits(
        local, rev_start=0, use_single_process=use_single_process
    )
    assert len(list(repository.get_commits())) == 2

    os.remove("data/commits.json")
    shutil.rmtree("data/commit_experiences.lmdb")
    commits = repository.download_commits(
        local,
        revs=[],
        use_single_process=use_single_process,
    )
    assert len(commits) == 0
    assert len(list(repository.get_commits())) == 0


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


@pytest.fixture
def ignored_commits_to_test(fake_hg_repo):
    hg, local, remote = fake_hg_repo

    repository.path_to_component = {}

    add_file(
        hg,
        local,
        ".hg-annotate-ignore-revs",
        "commit1\ncommit2\n8ba995b74e18334ab3707f27e9eb8f4e37ba3d29\n",
    )
    commit(hg)

    def create_commit(node, desc, bug_id, backsout, backedoutby):
        return repository.Commit(
            node=node,
            author="author",
            desc=desc,
            pushdate=datetime(2019, 1, 1),
            bug_id=bug_id,
            backsout=backsout,
            backedoutby=backedoutby,
            author_email="author@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp"], {})

    commits = [
        create_commit("commit", "", 123, [], ""),
        create_commit("commit_backout", "", 123, ["commit_backedout"], ""),
        create_commit("commit_backedout", "", 123, [], "commit_backout"),
        create_commit("commit_no_bug", "", None, [], ""),
        create_commit(
            "8ba995b74e18334ab3707f27e9eb8f4e37ba3d29",
            "commit in .hg-annotate-ignore-revs",
            123,
            [],
            "",
        ),
        create_commit(
            "commit_with_ignore_in_desc", "prova\nignore-this-changeset\n", 123, [], ""
        ),
    ]

    return hg, local, commits


def test_set_commits_to_ignore(ignored_commits_to_test):
    hg, local, commits = ignored_commits_to_test

    repository.set_commits_to_ignore(hg, local, commits)

    assert set(commit.node for commit in commits if commit.ignored) == {
        "8ba995b74e18334ab3707f27e9eb8f4e37ba3d29",
        "commit_with_ignore_in_desc",
    }


def test_filter_commits(ignored_commits_to_test):
    hg, local, commits = ignored_commits_to_test

    repository.set_commits_to_ignore(hg, local, commits)

    commits = [commit.to_dict() for commit in commits]

    assert set(commit["node"] for commit in repository.filter_commits(commits)) == {
        "commit_backedout",
        "commit",
    }

    assert set(
        commit["node"]
        for commit in repository.filter_commits(commits, include_no_bug=True)
    ) == {"commit_backedout", "commit", "commit_no_bug"}

    assert set(
        commit["node"]
        for commit in repository.filter_commits(commits, include_backouts=True)
    ) == {"commit_backedout", "commit", "commit_backout"}

    assert set(
        commit["node"]
        for commit in repository.filter_commits(commits, include_ignored=True)
    ) == {
        "commit_backedout",
        "commit",
        "8ba995b74e18334ab3707f27e9eb8f4e37ba3d29",
        "commit_with_ignore_in_desc",
    }

    assert set(
        commit["node"]
        for commit in repository.filter_commits(
            commits, include_no_bug=True, include_ignored=True
        )
    ) == {
        "commit_backedout",
        "commit",
        "commit_no_bug",
        "8ba995b74e18334ab3707f27e9eb8f4e37ba3d29",
        "commit_with_ignore_in_desc",
    }

    assert set(
        commit["node"]
        for commit in repository.filter_commits(
            commits, include_no_bug=True, include_backouts=True, include_ignored=True
        )
    ) == {
        "commit_backedout",
        "commit",
        "commit_no_bug",
        "commit_backout",
        "8ba995b74e18334ab3707f27e9eb8f4e37ba3d29",
        "commit_with_ignore_in_desc",
    }


def test_calculate_experiences() -> None:
    repository.path_to_component = {
        b"dom/file1.cpp": memoryview(b"Core::DOM"),
        b"dom/file1copied.cpp": memoryview(b"Core::DOM"),
        b"dom/file2.cpp": memoryview(b"Core::Layout"),
        b"apps/file1.jsm": memoryview(b"Firefox::Boh"),
        b"apps/file2.jsm": memoryview(b"Firefox::Boh"),
    }

    commits = {
        "commit1": repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "apps/file1.jsm"], {}),
        "commitbackedout": repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "apps/file1.jsm"], {}),
        "commit2": repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1"],
        ).set_files(["dom/file1.cpp"], {}),
        "commit2refactoring": repository.Commit(
            node="commit2refactoring",
            author="author2",
            desc="commit2refactoring",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1"],
            ignored=True,
        ).set_files(["dom/file1.cpp"], {}),
        "commit3": repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer2"],
        ).set_files(["dom/file2.cpp", "apps/file1.jsm"], {}),
        "commit4": repository.Commit(
            node="commit4",
            author="author2",
            desc="commit4",
            pushdate=datetime(2020, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "apps/file2.jsm"], {}),
        "commit5": repository.Commit(
            node="commit5",
            author="author3",
            desc="commit5",
            pushdate=datetime(2020, 1, 2),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author3@mozilla.org",
            reviewers=["reviewer3"],
        ).set_files(["dom/file1.cpp"], {"dom/file1.cpp": "dom/file1copied.cpp"}),
        "commit6": repository.Commit(
            node="commit6",
            author="author3",
            desc="commit6",
            pushdate=datetime(2020, 1, 3),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author3@mozilla.org",
            reviewers=["reviewer3"],
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

    commits["commit1"].pushdate = datetime(2020, 1, 4)
    commits["commit1"].node = "commit1copy"
    commits["commitbackedout"].pushdate = datetime(2020, 1, 4)
    commits["commitbackedout"].node = "commitbackedoutcopy"
    commits["commit2"].pushdate = datetime(2020, 1, 4)
    commits["commit2"].node = "commit2copy"
    commits["commit2refactoring"].pushdate = datetime(2020, 1, 4)
    commits["commit2refactoring"].node = "commit2refactoringcopy"
    commits["commit3"].pushdate = datetime(2020, 1, 4)
    commits["commit3"].node = "commit3copy"
    commits["commit4"].pushdate = datetime(2021, 1, 4)
    commits["commit4"].node = "commit4copy"
    commits["commit5"].pushdate = datetime(2021, 1, 5)
    commits["commit5"].node = "commit5copy"
    commits["commit6"].pushdate = datetime(2021, 1, 6)
    commits["commit6"].node = "commit6copy"

    repository.calculate_experiences(commits.values(), datetime(2019, 1, 1))

    assert commits["commit1"].seniority_author == 86400.0 * 368
    assert commits["commitbackedout"].seniority_author == 86400.0 * 368
    assert commits["commit2"].seniority_author == 86400.0 * 368
    assert commits["commit3"].seniority_author == 86400.0 * 368
    assert commits["commit4"].seniority_author == 86400.0 * 734
    assert commits["commit5"].seniority_author == 86400.0 * 369
    assert commits["commit6"].seniority_author == 86400.0 * 370

    assert commits["commit1"].touched_prev_total_author_sum == 2
    assert commits["commit2"].touched_prev_total_author_sum == 2
    assert commits["commit3"].touched_prev_total_author_sum == 3
    assert commits["commit4"].touched_prev_total_author_sum == 3
    assert commits["commit5"].touched_prev_total_author_sum == 2
    assert commits["commit6"].touched_prev_total_author_sum == 3

    assert commits["commit1"].touched_prev_90_days_author_sum == 0
    assert commits["commit2"].touched_prev_90_days_author_sum == 1
    assert commits["commit3"].touched_prev_90_days_author_sum == 1
    assert commits["commit4"].touched_prev_90_days_author_sum == 0
    assert commits["commit5"].touched_prev_90_days_author_sum == 0
    assert commits["commit6"].touched_prev_90_days_author_sum == 1

    assert commits["commit1"].touched_prev_total_reviewer_sum == 6
    assert commits["commit1"].touched_prev_total_reviewer_max == 3
    assert commits["commit1"].touched_prev_total_reviewer_min == 3
    assert commits["commit2"].touched_prev_total_reviewer_sum == 4
    assert commits["commit2"].touched_prev_total_reviewer_max == 4
    assert commits["commit2"].touched_prev_total_reviewer_min == 4
    assert commits["commit3"].touched_prev_total_reviewer_sum == 4
    assert commits["commit3"].touched_prev_total_reviewer_max == 4
    assert commits["commit3"].touched_prev_total_reviewer_min == 4
    assert commits["commit4"].touched_prev_total_reviewer_sum == 10
    assert commits["commit4"].touched_prev_total_reviewer_max == 5
    assert commits["commit4"].touched_prev_total_reviewer_min == 5
    assert commits["commit5"].touched_prev_total_reviewer_sum == 2
    assert commits["commit5"].touched_prev_total_reviewer_max == 2
    assert commits["commit5"].touched_prev_total_reviewer_min == 2
    assert commits["commit6"].touched_prev_total_reviewer_sum == 3
    assert commits["commit6"].touched_prev_total_reviewer_max == 3
    assert commits["commit6"].touched_prev_total_reviewer_min == 3

    assert commits["commit1"].touched_prev_90_days_reviewer_sum == 2
    assert commits["commit1"].touched_prev_90_days_reviewer_max == 1
    assert commits["commit1"].touched_prev_90_days_reviewer_min == 1
    assert commits["commit2"].touched_prev_90_days_reviewer_sum == 2
    assert commits["commit2"].touched_prev_90_days_reviewer_max == 2
    assert commits["commit2"].touched_prev_90_days_reviewer_min == 2
    assert commits["commit3"].touched_prev_90_days_reviewer_sum == 2
    assert commits["commit3"].touched_prev_90_days_reviewer_max == 2
    assert commits["commit3"].touched_prev_90_days_reviewer_min == 2
    assert commits["commit4"].touched_prev_90_days_reviewer_sum == 0
    assert commits["commit4"].touched_prev_90_days_reviewer_max == 0
    assert commits["commit4"].touched_prev_90_days_reviewer_min == 0
    assert commits["commit5"].touched_prev_90_days_reviewer_sum == 0
    assert commits["commit5"].touched_prev_90_days_reviewer_max == 0
    assert commits["commit5"].touched_prev_90_days_reviewer_min == 0
    assert commits["commit6"].touched_prev_90_days_reviewer_sum == 1
    assert commits["commit6"].touched_prev_90_days_reviewer_max == 1
    assert commits["commit6"].touched_prev_90_days_reviewer_min == 1

    assert commits["commit1"].touched_prev_total_file_sum == 6
    assert commits["commit1"].touched_prev_total_file_max == 5
    assert commits["commit1"].touched_prev_total_file_min == 2
    assert commits["commit2"].touched_prev_total_file_sum == 6
    assert commits["commit2"].touched_prev_total_file_max == 6
    assert commits["commit2"].touched_prev_total_file_min == 6
    assert commits["commit3"].touched_prev_total_file_sum == 3
    assert commits["commit3"].touched_prev_total_file_max == 3
    assert commits["commit3"].touched_prev_total_file_min == 1
    assert commits["commit4"].touched_prev_total_file_sum == 7
    assert commits["commit4"].touched_prev_total_file_max == 7
    assert commits["commit4"].touched_prev_total_file_min == 1
    assert commits["commit5"].touched_prev_total_file_sum == 8
    assert commits["commit5"].touched_prev_total_file_max == 8
    assert commits["commit5"].touched_prev_total_file_min == 8
    assert commits["commit6"].touched_prev_total_file_sum == 9
    assert commits["commit6"].touched_prev_total_file_max == 9
    assert commits["commit6"].touched_prev_total_file_min == 8

    assert commits["commit1"].touched_prev_90_days_file_sum == 3
    assert commits["commit1"].touched_prev_90_days_file_max == 3
    assert commits["commit1"].touched_prev_90_days_file_min == 0
    assert commits["commit2"].touched_prev_90_days_file_sum == 4
    assert commits["commit2"].touched_prev_90_days_file_max == 4
    assert commits["commit2"].touched_prev_90_days_file_min == 4
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

    assert commits["commit1"].touched_prev_total_directory_sum == 6
    assert commits["commit1"].touched_prev_total_directory_max == 6
    assert commits["commit1"].touched_prev_total_directory_min == 3
    assert commits["commit2"].touched_prev_total_directory_sum == 7
    assert commits["commit2"].touched_prev_total_directory_max == 7
    assert commits["commit2"].touched_prev_total_directory_min == 7
    assert commits["commit3"].touched_prev_total_directory_sum == 8
    assert commits["commit3"].touched_prev_total_directory_max == 8
    assert commits["commit3"].touched_prev_total_directory_min == 4
    assert commits["commit4"].touched_prev_total_directory_sum == 9
    assert commits["commit4"].touched_prev_total_directory_max == 9
    assert commits["commit4"].touched_prev_total_directory_min == 5
    assert commits["commit5"].touched_prev_total_directory_sum == 10
    assert commits["commit5"].touched_prev_total_directory_max == 10
    assert commits["commit5"].touched_prev_total_directory_min == 10
    assert commits["commit6"].touched_prev_total_directory_sum == 11
    assert commits["commit6"].touched_prev_total_directory_max == 11
    assert commits["commit6"].touched_prev_total_directory_min == 11

    assert commits["commit1"].touched_prev_90_days_directory_sum == 3
    assert commits["commit1"].touched_prev_90_days_directory_max == 3
    assert commits["commit1"].touched_prev_90_days_directory_min == 1
    assert commits["commit2"].touched_prev_90_days_directory_sum == 4
    assert commits["commit2"].touched_prev_90_days_directory_max == 4
    assert commits["commit2"].touched_prev_90_days_directory_min == 4
    assert commits["commit3"].touched_prev_90_days_directory_sum == 5
    assert commits["commit3"].touched_prev_90_days_directory_max == 5
    assert commits["commit3"].touched_prev_90_days_directory_min == 2
    assert commits["commit4"].touched_prev_90_days_directory_sum == 0
    assert commits["commit4"].touched_prev_90_days_directory_max == 0
    assert commits["commit4"].touched_prev_90_days_directory_min == 0
    assert commits["commit5"].touched_prev_90_days_directory_sum == 1
    assert commits["commit5"].touched_prev_90_days_directory_max == 1
    assert commits["commit5"].touched_prev_90_days_directory_min == 1
    assert commits["commit6"].touched_prev_90_days_directory_sum == 2
    assert commits["commit6"].touched_prev_90_days_directory_max == 2
    assert commits["commit6"].touched_prev_90_days_directory_min == 2

    assert commits["commit1"].touched_prev_total_component_sum == 6
    assert commits["commit1"].touched_prev_total_component_max == 5
    assert commits["commit1"].touched_prev_total_component_min == 3
    assert commits["commit2"].touched_prev_total_component_sum == 6
    assert commits["commit2"].touched_prev_total_component_max == 6
    assert commits["commit2"].touched_prev_total_component_min == 6
    assert commits["commit3"].touched_prev_total_component_sum == 4
    assert commits["commit3"].touched_prev_total_component_max == 4
    assert commits["commit3"].touched_prev_total_component_min == 1
    assert commits["commit4"].touched_prev_total_component_sum == 9
    assert commits["commit4"].touched_prev_total_component_max == 7
    assert commits["commit4"].touched_prev_total_component_min == 5
    assert commits["commit5"].touched_prev_total_component_sum == 8
    assert commits["commit5"].touched_prev_total_component_max == 8
    assert commits["commit5"].touched_prev_total_component_min == 8
    assert commits["commit6"].touched_prev_total_component_sum == 9
    assert commits["commit6"].touched_prev_total_component_max == 9
    assert commits["commit6"].touched_prev_total_component_min == 9

    assert commits["commit1"].touched_prev_90_days_component_sum == 3
    assert commits["commit1"].touched_prev_90_days_component_max == 3
    assert commits["commit1"].touched_prev_90_days_component_min == 1
    assert commits["commit2"].touched_prev_90_days_component_sum == 4
    assert commits["commit2"].touched_prev_90_days_component_max == 4
    assert commits["commit2"].touched_prev_90_days_component_min == 4
    assert commits["commit3"].touched_prev_90_days_component_sum == 2
    assert commits["commit3"].touched_prev_90_days_component_max == 2
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

    with pytest.raises(
        Exception, match=r"Can't get a day \(368\) from earlier than start day \(644\)"
    ):
        repository.calculate_experiences(commits.values(), datetime(2019, 1, 1))


def test_calculate_experiences_no_save() -> None:
    repository.path_to_component = {
        b"dom/file1.cpp": memoryview(b"Core::DOM"),
        b"dom/file1copied.cpp": memoryview(b"Core::DOM"),
        b"dom/file2.cpp": memoryview(b"Core::Layout"),
        b"apps/file1.jsm": memoryview(b"Firefox::Boh"),
        b"apps/file2.jsm": memoryview(b"Firefox::Boh"),
    }

    commits = {
        "commit1": repository.Commit(
            node="commit1",
            author="author1",
            desc="commit1",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "apps/file1.jsm"], {}),
        "commitbackedout": repository.Commit(
            node="commitbackedout",
            author="author1",
            desc="commitbackedout",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="commitbackout",
            author_email="author1@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "apps/file1.jsm"], {}),
        "commit2": repository.Commit(
            node="commit2",
            author="author2",
            desc="commit2",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1"],
        ).set_files(["dom/file1.cpp"], {}),
        "commit2refactoring": repository.Commit(
            node="commit2refactoring",
            author="author2",
            desc="commit2refactoring",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1"],
            ignored=True,
        ).set_files(["dom/file1.cpp"], {}),
        "commit3": repository.Commit(
            node="commit3",
            author="author1",
            desc="commit3",
            pushdate=datetime(2019, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author1@mozilla.org",
            reviewers=["reviewer2"],
        ).set_files(["dom/file2.cpp", "apps/file1.jsm"], {}),
        "commit4": repository.Commit(
            node="commit4",
            author="author2",
            desc="commit4",
            pushdate=datetime(2020, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author2@mozilla.org",
            reviewers=["reviewer1", "reviewer2"],
        ).set_files(["dom/file1.cpp", "apps/file2.jsm"], {}),
        "commit5": repository.Commit(
            node="commit5",
            author="author3",
            desc="commit5",
            pushdate=datetime(2020, 1, 2),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author3@mozilla.org",
            reviewers=["reviewer3"],
        ).set_files(["dom/file1.cpp"], {"dom/file1.cpp": "dom/file1copied.cpp"}),
        "commit6": repository.Commit(
            node="commit6",
            author="author3",
            desc="commit6",
            pushdate=datetime(2020, 1, 3),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author3@mozilla.org",
            reviewers=["reviewer3"],
        ).set_files(["dom/file1.cpp", "dom/file1copied.cpp"], {}),
    }

    repository.calculate_experiences(commits.values(), datetime(2019, 1, 1), save=False)

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

    repository.calculate_experiences(commits.values(), datetime(2019, 1, 1), save=False)

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


def test_get_touched_functions():
    # Allow using the local code analysis server.
    responses.add_passthru("http://127.0.0.1")

    code_analysis_server = rust_code_analysis_server.RustCodeAnalysisServer()

    def _func_list_to_set(functions):
        return set(
            (func["name"], func["start_line"], func["end_line"]) for func in functions
        )

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func2() {
    int i = 2;
}""",
        unit=False,
    )

    # No function touched.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [],
        [],
    )

    assert touched_functions == []

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func2() {
    int i = 2;
}""",
        unit=False,
    )

    # A function touched by adding a line.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [],
        [1],
    )

    assert _func_list_to_set(touched_functions) == {("func1", 1, 3)}

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func3() {
    int i = 3;
}

void func4() {
    int i = 4;
}""",
        unit=False,
    )

    # A function touched by removing a line, another function touched by adding a line.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [2, 5, 6, 7, 8],
        [6],
    )

    assert _func_list_to_set(touched_functions) == {("func3", 5, 7), ("func1", 1, 3)}

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func2() {
    int i = 2;
}""",
        unit=False,
    )

    # A function touched by replacing a line.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [6],
        [6],
    )

    assert _func_list_to_set(touched_functions) == {("func2", 5, 7)}

    metrics = code_analysis_server.metrics(
        "file.js",
        """let j = 0;

function func() {
let i = 0;
}""",
        unit=False,
    )

    # top-level and a JavaScript function touched.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [],
        [1, 4],
    )

    assert _func_list_to_set(touched_functions) == {("func", 3, 5)}

    metrics = code_analysis_server.metrics(
        "file.jsm",
        """function outer_func() {
let i = 0;
let f = function() {
  let j = 0;
}();
}""",
        unit=False,
    )

    # An anonymous function touched inside another function.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [],
        [4],
    )

    assert _func_list_to_set(touched_functions) == {("outer_func", 1, 6)}

    metrics = code_analysis_server.metrics(
        "file.jsm",
        """function outer_func() {
let i = 0;
function inner_func() {
  let j = 0;
}
}""",
        unit=False,
    )

    # A function touched inside another function.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [],
        [4],
    )

    assert _func_list_to_set(touched_functions) == {
        ("outer_func", 1, 6),
        ("inner_func", 3, 5),
    }

    metrics = code_analysis_server.metrics(
        "file.jsm",
        """function outer_func() {
let i = 0;
function inner_func() {
  let j = 0;
}
}

function inner_func() {
  let j = 0;
}
""",
        unit=False,
    )

    # A function touched inside another function, and a function with the same name somewhere else.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [],
        [4],
    )

    assert _func_list_to_set(touched_functions) == {
        ("outer_func", 1, 6),
        ("inner_func", 3, 5),
    }

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func3() {
    int i = 3;
}

void func4() {
    int i = 4;
}""",
        unit=False,
    )

    # A function removed in the middle, no new function touched.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [5, 6, 7, 8],
        [],
    )

    assert touched_functions == []

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func2() {
    int i = 2;
}

void func3() {
    int i = 3;
}

void func4() {
    int i = 4;
}""",
        unit=False,
    )

    # A function removed at the beginning, no new function touched.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [1, 2, 3, 4],
        [],
    )

    assert touched_functions == []

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func2() {
    int i = 2;
}

void func3() {
    int i = 3;
}""",
        unit=False,
    )

    # A function removed at the end, no new function touched.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [12, 13, 14],
        [],
    )

    assert touched_functions == []

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func2() {
    int i = 2;
}

void func3() {
    int i = 3;
}

void func4() {
    int i = 4;
}""",
        unit=False,
    )

    # A function removed at the beginning, no new function touched.
    touched_functions = repository.get_touched_functions(
        metrics["spaces"],
        [1, 2, 3, 4],
        [1, 2, 3, 4],
    )

    assert _func_list_to_set(touched_functions) == {("func1", 1, 3)}


def test_get_space_metrics():
    # Allow using the local code analysis server.
    responses.add_passthru("http://127.0.0.1")

    commit = repository.Commit(
        node="commit",
        author="author",
        desc="commit",
        pushdate=datetime(2021, 1, 1),
        bug_id=123,
        backsout=[],
        backedoutby="",
        author_email="author@mozilla.org",
        reviewers=["reviewer"],
    ).set_files([], {})

    code_analysis_server = rust_code_analysis_server.RustCodeAnalysisServer()

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func2() {
    int i = 2;
}""",
        unit=False,
    )

    repository.get_space_metrics(commit.metrics, metrics["spaces"])
    # TODO: Assert metrics.

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func2() {
    int i = 2;
}""",
        unit=False,
    )

    repository.get_space_metrics(commit.metrics, metrics["spaces"])
    # TODO: Assert metrics.

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func3() {
    int i = 3;
}

void func4() {
    int i = 4;
}""",
        unit=False,
    )

    repository.get_space_metrics(commit.metrics, metrics["spaces"])
    # TODO: Assert metrics.

    metrics = code_analysis_server.metrics(
        "file.cpp",
        """void func1() {
    int i = 1;
}

void func2() {
    int i = 2;
}""",
        unit=False,
    )

    repository.get_space_metrics(commit.metrics, metrics["spaces"])
    # TODO: Assert metrics.

    metrics = code_analysis_server.metrics(
        "file.js",
        """let j = 0;

function func() {
let i = 0;
}""",
        unit=False,
    )

    repository.get_space_metrics(commit.metrics, metrics["spaces"])
    # TODO: Assert metrics.

    metrics = code_analysis_server.metrics(
        "file.jsm",
        """function outer_func() {
let i = 0;
let f = function() {
  let j = 0;
}();
}""",
        unit=False,
    )

    repository.get_space_metrics(commit.metrics, metrics["spaces"])
    # TODO: Assert metrics.

    metrics = code_analysis_server.metrics(
        "file.jsm",
        """function outer_func() {
let i = 0;
function inner_func() {
  let j = 0;
}
}""",
        unit=False,
    )

    repository.get_space_metrics(commit.metrics, metrics["spaces"])
    # TODO: Assert metrics.


def test_get_commits():
    # By default get_commits utilizes the following parameters:
    # include_no_bug: bool = False
    # include_backouts: bool = False
    # include_ignored: bool = False

    BACKOUT_COMMIT = "ec01c146f756b74d18e4892b4fd3aecba00da93e"
    IGNORED_COMMIT = "7f27080ffee35521c42fe9d4025caabef7b6258c"
    NOBUG_COMMIT = "75966ee1fe658b1767d7459256175c0662d14c25"
    NOBUG_IGNORED_COMMIT = "75276e64701bfde7cf2dd1f851adfea6a92d5747"
    NOBUG_IGNORED_BACKOUT_COMMIT = "46c1c161cbe189a59d8274e011085d76163db7f4"

    retrieved_commits = list(repository.get_commits())
    excluded_commits = [
        IGNORED_COMMIT,
        BACKOUT_COMMIT,
        NOBUG_COMMIT,
        NOBUG_IGNORED_COMMIT,
        NOBUG_IGNORED_BACKOUT_COMMIT,
    ]
    # 10 mock commits, 1 ignored, 1 backouts, 1 no_bug, 1 no_bug and ignored, 1 no_bug, ignored, and backouts
    assert len(retrieved_commits) == 5
    assert not any(
        excluded_commit in {c["node"] for c in retrieved_commits}
        for excluded_commit in excluded_commits
    )

    retrieved_commits = list(repository.get_commits(include_backouts=True))
    assert len(retrieved_commits) == 6
    assert BACKOUT_COMMIT in (c["node"] for c in retrieved_commits)

    retrieved_commits = list(repository.get_commits(include_ignored=True))
    assert len(retrieved_commits) == 6
    assert IGNORED_COMMIT in (c["node"] for c in retrieved_commits)

    retrieved_commits = list(repository.get_commits(include_no_bug=True))
    assert len(retrieved_commits) == 6
    assert NOBUG_COMMIT in (c["node"] for c in retrieved_commits)

    retrieved_commits = list(
        repository.get_commits(include_ignored=True, include_backouts=True)
    )
    included_commits = {IGNORED_COMMIT, BACKOUT_COMMIT}
    assert len(retrieved_commits) == 7
    assert included_commits.issubset({c["node"] for c in retrieved_commits})

    retrieved_commits = list(
        repository.get_commits(include_ignored=True, include_no_bug=True)
    )
    included_commits = {IGNORED_COMMIT, NOBUG_COMMIT}
    assert len(retrieved_commits) == 8
    assert included_commits.issubset({c["node"] for c in retrieved_commits})

    retrieved_commits = list(
        repository.get_commits(include_no_bug=True, include_backouts=True)
    )
    included_commits = {BACKOUT_COMMIT, NOBUG_COMMIT}
    assert len(retrieved_commits) == 7
    assert included_commits.issubset({c["node"] for c in retrieved_commits})

    retrieved_commits = list(
        repository.get_commits(
            include_ignored=True, include_backouts=True, include_no_bug=True
        )
    )
    included_commits = {
        IGNORED_COMMIT,
        BACKOUT_COMMIT,
        NOBUG_COMMIT,
        NOBUG_IGNORED_COMMIT,
        NOBUG_IGNORED_BACKOUT_COMMIT,
    }
    assert len(retrieved_commits) == 10
    assert included_commits.issubset({c["node"] for c in retrieved_commits})


def test_get_revision_id():
    commit = {
        "desc": "My desc",
    }
    assert repository.get_revision_id(commit) is None

    commit = {
        "desc": "Bug 1667333: Remove unnecessary prefs for mime type checking r=necko-reviewers,evilpie,valentin\n\nDifferential Revision: https://phabricator.services.mozilla.com/D91406",
    }
    assert repository.get_revision_id(commit) == 91406


def test_commit_dict_matches_expected():
    commit = (
        repository.Commit(
            node="commit",
            author="author",
            desc="commit",
            pushdate=datetime(2021, 1, 1),
            bug_id=123,
            backsout=[],
            backedoutby="",
            author_email="author@mozilla.org",
            reviewers=["reviewer"],
        )
        .set_files([], {})
        .to_dict()
    )

    # Ensure the fields expected by commit_features functions are actually present in the commit dict.
    commit_features.SourceCodeFileMetrics()(commit)
    commit_features.SourceCodeFunctionMetrics()(commit)
    commit_features.FunctionsTouchedSize()(commit)
    commit_features.SourceCodeMetricsDiff()(commit)

    merged_commit = commit_features.merge_commits([commit, commit])
    commit_features.SourceCodeFileMetrics()(merged_commit)
    # TODO: Support merging functions in commit_features.merge_commits
    # commit_features.source_code_function_metrics()(merged_commit)
    # TODO: Support merging diff metrics in commit_features.merge_commits
    # commit_features.source_code_metrics_diff()(commit)

    commit = repository.Commit(
        node="commit",
        author="author",
        desc="commit",
        pushdate=datetime(2021, 1, 1),
        bug_id=123,
        backsout=[],
        backedoutby="",
        author_email="author@mozilla.org",
        reviewers=["reviewer"],
    ).set_files([], {})

    commit.functions["file"] = [
        {
            "name": "ciao",
            "start": 1,
            "end": 5,
            "metrics": {
                key: value
                for key, value in repository.get_metrics_dict().items()
                if key.endswith("_total")
            },
        },
        {
            "name": "ciao2",
            "start": 6,
            "end": 42,
            "metrics": {
                key: value
                for key, value in repository.get_metrics_dict().items()
                if key.endswith("_total")
            },
        },
    ]

    commit = commit.to_dict()

    commit_features.SourceCodeFunctionMetrics()(commit)
    commit_features.FunctionsTouchedSize()(commit)

    merged_commit = commit_features.merge_commits([commit, commit])
    commit_features.SourceCodeFileMetrics()(merged_commit)
    # TODO: Support merging functions in commit_features.merge_commits
    # commit_features.source_code_function_metrics()(merged_commit)
    # TODO: Support merging diff metrics in commit_features.merge_commits
    # commit_features.source_code_metrics_diff()(commit)


def test_set_commit_metrics():
    # Allow using the local code analysis server.
    responses.add_passthru("http://127.0.0.1")

    commit = repository.Commit(
        node="commit",
        author="author",
        desc="commit",
        pushdate=datetime(2021, 1, 1),
        bug_id=123,
        backsout=[],
        backedoutby="",
        author_email="author@mozilla.org",
        reviewers=["reviewer"],
    ).set_files([], {})

    before = """#include <iostream>

void main() {
    cout << "Ciao1" << endl;
}
"""

    after = """#include <iostream>

void main() {
    cout << "Ciao2" << endl;
}
"""

    patch = """--- prova.cpp	2021-02-03 11:32:47.398531225 +0100
+++ prova.cpp	2021-02-03 11:32:45.114531901 +0100
@@ -1,6 +1,6 @@
 #include <iostream>
 
 void main() {
-    cout << "Ciao1" << endl;
+    cout << "Ciao2" << endl;
 }
 """  # noqa: W293

    patch_data = rs_parsepatch.get_lines(patch)

    logger.info(patch_data)

    assert len(patch_data) == 1

    code_analysis_server = rust_code_analysis_server.RustCodeAnalysisServer()

    stats = patch_data[0]

    path = stats["filename"]

    after_metrics = code_analysis_server.metrics(path, after, unit=False)
    before_metrics = code_analysis_server.metrics(path, before, unit=False)

    repository.set_commit_metrics(
        commit,
        "prova.cpp",
        stats["deleted_lines"],
        stats["added_lines"],
        before_metrics,
        after_metrics,
    )

    assert commit.metrics == {
        "cloc_avg": 0.0,
        "cloc_max": 0,
        "cloc_min": 0.0,
        "cloc_total": 0.0,
        "cognitive_avg": 0.0,
        "cognitive_max": 0,
        "cognitive_min": 0.0,
        "cognitive_total": 0.0,
        "cyclomatic_avg": 0.0,
        "cyclomatic_max": 1.0,
        "cyclomatic_min": 1.0,
        "cyclomatic_total": 2.0,
        "halstead_N1_avg": 0.0,
        "halstead_N1_max": 6.0,
        "halstead_N1_min": 6.0,
        "halstead_N1_total": 6.0,
        "halstead_N2_avg": 0.0,
        "halstead_N2_max": 4.0,
        "halstead_N2_min": 4.0,
        "halstead_N2_total": 4.0,
        "halstead_n1_avg": 0.0,
        "halstead_n1_max": 5.0,
        "halstead_n1_min": 5.0,
        "halstead_n1_total": 5.0,
        "halstead_n2_avg": 0.0,
        "halstead_n2_max": 4.0,
        "halstead_n2_min": 4.0,
        "halstead_n2_total": 4.0,
        "halstead_length_avg": 0.0,
        "halstead_length_max": 10.0,
        "halstead_length_min": 10.0,
        "halstead_length_total": 10.0,
        "halstead_estimated_program_length_avg": 0.0,
        "halstead_estimated_program_length_max": 19.60964047443681,
        "halstead_estimated_program_length_min": 19.60964047443681,
        "halstead_estimated_program_length_total": 19.60964047443681,
        "halstead_purity_ratio_avg": 0.0,
        "halstead_purity_ratio_max": 1.960964047443681,
        "halstead_purity_ratio_min": 1.960964047443681,
        "halstead_purity_ratio_total": 1.960964047443681,
        "halstead_vocabulary_avg": 0.0,
        "halstead_vocabulary_max": 9.0,
        "halstead_vocabulary_min": 9.0,
        "halstead_vocabulary_total": 9.0,
        "halstead_volume_avg": 0.0,
        "halstead_volume_max": 31.69925001442312,
        "halstead_volume_min": 31.69925001442312,
        "halstead_volume_total": 31.69925001442312,
        "halstead_difficulty_avg": 0.0,
        "halstead_difficulty_max": 2.5,
        "halstead_difficulty_min": 2.5,
        "halstead_difficulty_total": 2.5,
        "halstead_level_avg": 0.0,
        "halstead_level_max": 0.4,
        "halstead_level_min": 0.4,
        "halstead_level_total": 0.4,
        "halstead_effort_avg": 0.0,
        "halstead_effort_max": 79.2481250360578,
        "halstead_effort_min": 79.2481250360578,
        "halstead_effort_total": 79.2481250360578,
        "halstead_time_avg": 0.0,
        "halstead_time_max": 4.402673613114322,
        "halstead_time_min": 4.402673613114322,
        "halstead_time_total": 4.402673613114322,
        "halstead_bugs_avg": 0.0,
        "halstead_bugs_max": 0.006149947511600477,
        "halstead_bugs_min": 0.006149947511600477,
        "halstead_bugs_total": 0.006149947511600477,
        "functions_avg": 0.0,
        "functions_max": 1.0,
        "functions_min": 1.0,
        "functions_total": 1.0,
        "closures_avg": 0.0,
        "closures_max": 0,
        "closures_min": 0.0,
        "closures_total": 0.0,
        "lloc_avg": 0.0,
        "lloc_max": 1.0,
        "lloc_min": 1.0,
        "lloc_total": 1.0,
        "nargs_avg": 0.0,
        "nargs_max": 0,
        "nargs_min": 0.0,
        "nargs_total": 0.0,
        "nexits_avg": 0.0,
        "nexits_max": 0,
        "nexits_min": 0.0,
        "nexits_total": 0.0,
        "ploc_avg": 0.0,
        "ploc_max": 3.0,
        "ploc_min": 3.0,
        "ploc_total": 4.0,
        "sloc_avg": 0.0,
        "sloc_max": 3.0,
        "sloc_min": 3.0,
        "sloc_total": 5.0,
        "mi_original_avg": 0.0,
        "mi_original_max": 134.9997572104644,
        "mi_original_min": 134.9997572104644,
        "mi_original_total": 126.49438210545534,
        "mi_sei_avg": 0.0,
        "mi_sei_max": 119.16444811614275,
        "mi_sei_min": 119.16444811614275,
        "mi_sei_total": 106.9956054906502,
        "mi_visual_studio_avg": 0.0,
        "mi_visual_studio_max": 78.94722643886807,
        "mi_visual_studio_min": 78.94722643886807,
        "mi_visual_studio_total": 73.97332286868733,
        "blank_avg": 0.0,
        "blank_max": 0.0,
        "blank_min": 0.0,
        "blank_total": 1.0,
    }
    assert commit.metrics_diff == {
        "cloc_total": 0.0,
        "cognitive_total": 0.0,
        "cyclomatic_total": 0.0,
        "halstead_N1_total": 0.0,
        "halstead_N2_total": 0.0,
        "halstead_n1_total": 0.0,
        "halstead_n2_total": 0.0,
        "halstead_length_total": 0.0,
        "halstead_estimated_program_length_total": 0.0,
        "halstead_purity_ratio_total": 0.0,
        "halstead_vocabulary_total": 0.0,
        "halstead_volume_total": 0.0,
        "halstead_difficulty_total": 0.0,
        "halstead_level_total": 0.0,
        "halstead_effort_total": 0.0,
        "halstead_time_total": 0.0,
        "halstead_bugs_total": 0.0,
        "functions_total": 0.0,
        "closures_total": 0.0,
        "lloc_total": 0.0,
        "nargs_total": 0.0,
        "nexits_total": 0.0,
        "ploc_total": 0.0,
        "sloc_total": 0.0,
        "mi_original_total": 0.0,
        "mi_sei_total": 0.0,
        "mi_visual_studio_total": 0.0,
        "blank_total": 0.0,
    }
    assert commit.functions == {
        "prova.cpp": [
            {
                "end": 5,
                "metrics": {
                    "cloc_total": 0.0,
                    "cognitive_total": 0.0,
                    "cyclomatic_total": 1.0,
                    "halstead_N1_total": 6.0,
                    "halstead_N2_total": 4.0,
                    "halstead_n1_total": 5.0,
                    "halstead_n2_total": 4.0,
                    "halstead_length_total": 10.0,
                    "halstead_estimated_program_length_total": 19.60964047443681,
                    "halstead_purity_ratio_total": 1.960964047443681,
                    "halstead_vocabulary_total": 9.0,
                    "halstead_volume_total": 31.69925001442312,
                    "halstead_difficulty_total": 2.5,
                    "halstead_level_total": 0.4,
                    "halstead_effort_total": 79.2481250360578,
                    "halstead_time_total": 4.402673613114322,
                    "halstead_bugs_total": 0.006149947511600477,
                    "functions_total": 1.0,
                    "closures_total": 0.0,
                    "lloc_total": 1.0,
                    "nargs_total": 0.0,
                    "nexits_total": 0.0,
                    "ploc_total": 3.0,
                    "sloc_total": 3.0,
                    "mi_original_total": 134.9997572104644,
                    "mi_sei_total": 119.16444811614275,
                    "mi_visual_studio_total": 78.94722643886807,
                    "blank_total": 0.0,
                },
                "name": "main",
                "start": 3,
            }
        ]
    }


def test_set_commit_metrics_meaningful_diff():
    # Allow using the local code analysis server.
    responses.add_passthru("http://127.0.0.1")

    commit = repository.Commit(
        node="commit",
        author="author",
        desc="commit",
        pushdate=datetime(2021, 1, 1),
        bug_id=123,
        backsout=[],
        backedoutby="",
        author_email="author@mozilla.org",
        reviewers=["reviewer"],
    ).set_files([], {})

    before = """#include <iostream>

void main() {
    cout << "Ciao1" << endl;
}
"""

    after = """#include <iostream>

void main() {
    cout << "Ciao1" << endl;
    cout << "Ciao2" << endl;
}
"""

    patch = """--- prova.cpp	2021-02-03 11:32:47.398531225 +0100
+++ prova.cpp	2021-02-03 11:32:45.114531901 +0100
@@ -1,6 +1,6 @@
 #include <iostream>
 
 void main() {
     cout << "Ciao1" << endl;
+    cout << "Ciao2" << endl;
 }
 """  # noqa: W293

    patch_data = rs_parsepatch.get_lines(patch)

    logger.info(patch_data)

    assert len(patch_data) == 1

    code_analysis_server = rust_code_analysis_server.RustCodeAnalysisServer()

    stats = patch_data[0]

    path = stats["filename"]

    after_metrics = code_analysis_server.metrics(path, after, unit=False)
    before_metrics = code_analysis_server.metrics(path, before, unit=False)

    repository.set_commit_metrics(
        commit,
        "prova.cpp",
        stats["deleted_lines"],
        stats["added_lines"],
        before_metrics,
        after_metrics,
    )

    assert commit.metrics == {
        "cloc_avg": 0.0,
        "cloc_max": 0,
        "cloc_min": 0.0,
        "cloc_total": 0.0,
        "cognitive_avg": 0.0,
        "cognitive_max": 0,
        "cognitive_min": 0.0,
        "cognitive_total": 0.0,
        "cyclomatic_avg": 0.0,
        "cyclomatic_max": 1.0,
        "cyclomatic_min": 1.0,
        "cyclomatic_total": 2.0,
        "halstead_N1_avg": 0.0,
        "halstead_N1_max": 9.0,
        "halstead_N1_min": 9.0,
        "halstead_N1_total": 9.0,
        "halstead_N2_avg": 0.0,
        "halstead_N2_max": 7.0,
        "halstead_N2_min": 7.0,
        "halstead_N2_total": 7.0,
        "halstead_n1_avg": 0.0,
        "halstead_n1_max": 5.0,
        "halstead_n1_min": 5.0,
        "halstead_n1_total": 5.0,
        "halstead_n2_avg": 0.0,
        "halstead_n2_max": 5.0,
        "halstead_n2_min": 5.0,
        "halstead_n2_total": 5.0,
        "halstead_length_avg": 0.0,
        "halstead_length_max": 16.0,
        "halstead_length_min": 16.0,
        "halstead_length_total": 16.0,
        "halstead_estimated_program_length_avg": 0.0,
        "halstead_estimated_program_length_max": 23.21928094887362,
        "halstead_estimated_program_length_min": 23.21928094887362,
        "halstead_estimated_program_length_total": 23.21928094887362,
        "halstead_purity_ratio_avg": 0.0,
        "halstead_purity_ratio_max": 1.4512050593046013,
        "halstead_purity_ratio_min": 1.4512050593046013,
        "halstead_purity_ratio_total": 1.4512050593046013,
        "halstead_vocabulary_avg": 0.0,
        "halstead_vocabulary_max": 10.0,
        "halstead_vocabulary_min": 10.0,
        "halstead_vocabulary_total": 10.0,
        "halstead_volume_avg": 0.0,
        "halstead_volume_max": 53.150849518197795,
        "halstead_volume_min": 53.150849518197795,
        "halstead_volume_total": 53.150849518197795,
        "halstead_difficulty_avg": 0.0,
        "halstead_difficulty_max": 3.5,
        "halstead_difficulty_min": 3.5,
        "halstead_difficulty_total": 3.5,
        "halstead_level_avg": 0.0,
        "halstead_level_max": 0.2857142857142857,
        "halstead_level_min": 0.2857142857142857,
        "halstead_level_total": 0.2857142857142857,
        "halstead_effort_avg": 0.0,
        "halstead_effort_max": 186.02797331369229,
        "halstead_effort_min": 186.02797331369229,
        "halstead_effort_total": 186.02797331369229,
        "halstead_time_avg": 0.0,
        "halstead_time_max": 10.334887406316238,
        "halstead_time_min": 10.334887406316238,
        "halstead_time_total": 10.334887406316238,
        "halstead_bugs_avg": 0.0,
        "halstead_bugs_max": 0.010862528153285396,
        "halstead_bugs_min": 0.010862528153285396,
        "halstead_bugs_total": 0.010862528153285396,
        "functions_avg": 0.0,
        "functions_max": 1.0,
        "functions_min": 1.0,
        "functions_total": 1.0,
        "closures_avg": 0.0,
        "closures_max": 0,
        "closures_min": 0.0,
        "closures_total": 0.0,
        "lloc_avg": 0.0,
        "lloc_max": 2.0,
        "lloc_min": 2.0,
        "lloc_total": 2.0,
        "nargs_avg": 0.0,
        "nargs_max": 0,
        "nargs_min": 0.0,
        "nargs_total": 0.0,
        "nexits_avg": 0.0,
        "nexits_max": 0,
        "nexits_min": 0.0,
        "nexits_total": 0.0,
        "ploc_avg": 0.0,
        "ploc_max": 4.0,
        "ploc_min": 4.0,
        "ploc_total": 5.0,
        "sloc_avg": 0.0,
        "sloc_max": 4.0,
        "sloc_min": 4.0,
        "sloc_total": 6.0,
        "mi_original_avg": 0.0,
        "mi_original_max": 127.65173409189691,
        "mi_original_min": 127.65173409189691,
        "mi_original_total": 120.85319934054462,
        "mi_sei_avg": 0.0,
        "mi_sei_max": 108.56349160264799,
        "mi_sei_min": 108.56349160264799,
        "mi_sei_total": 98.85709909096525,
        "mi_visual_studio_avg": 0.0,
        "mi_visual_studio_max": 74.65013689584615,
        "mi_visual_studio_min": 74.65013689584615,
        "mi_visual_studio_total": 70.67438557926586,
        "blank_avg": 0.0,
        "blank_max": 0.0,
        "blank_min": 0.0,
        "blank_total": 1.0,
    }
    assert commit.metrics_diff == {
        "cloc_total": 0.0,
        "cognitive_total": 0.0,
        "cyclomatic_total": 0.0,
        "halstead_N1_total": 3.0,
        "halstead_N2_total": 3.0,
        "halstead_n1_total": 0.0,
        "halstead_n2_total": 1.0,
        "halstead_length_total": 6.0,
        "halstead_estimated_program_length_total": 3.6096404744368122,
        "halstead_purity_ratio_total": -0.5097589881390796,
        "halstead_vocabulary_total": 1.0,
        "halstead_volume_total": 21.451599503774673,
        "halstead_difficulty_total": 1.0,
        "halstead_level_total": -0.11428571428571432,
        "halstead_effort_total": 106.77984827763449,
        "halstead_time_total": 5.932213793201916,
        "halstead_bugs_total": 0.00471258064168492,
        "functions_total": 0.0,
        "closures_total": 0.0,
        "lloc_total": 1.0,
        "nargs_total": 0.0,
        "nexits_total": 0.0,
        "ploc_total": 1.0,
        "sloc_total": 1.0,
        "mi_original_total": -5.641182764910724,
        "mi_sei_total": -8.138506399684957,
        "mi_visual_studio_total": -3.298937289421474,
        "blank_total": 0.0,
    }
    assert commit.functions == {
        "prova.cpp": [
            {
                "end": 6,
                "metrics": {
                    "cloc_total": 0.0,
                    "cognitive_total": 0.0,
                    "cyclomatic_total": 1.0,
                    "halstead_N1_total": 9.0,
                    "halstead_N2_total": 7.0,
                    "halstead_n1_total": 5.0,
                    "halstead_n2_total": 5.0,
                    "halstead_length_total": 16.0,
                    "halstead_estimated_program_length_total": 23.21928094887362,
                    "halstead_purity_ratio_total": 1.4512050593046013,
                    "halstead_vocabulary_total": 10.0,
                    "halstead_volume_total": 53.150849518197795,
                    "halstead_difficulty_total": 3.5,
                    "halstead_level_total": 0.2857142857142857,
                    "halstead_effort_total": 186.02797331369229,
                    "halstead_time_total": 10.334887406316238,
                    "halstead_bugs_total": 0.010862528153285396,
                    "functions_total": 1.0,
                    "closures_total": 0.0,
                    "lloc_total": 2.0,
                    "nargs_total": 0.0,
                    "nexits_total": 0.0,
                    "ploc_total": 4.0,
                    "sloc_total": 4.0,
                    "mi_original_total": 127.65173409189691,
                    "mi_sei_total": 108.56349160264799,
                    "mi_visual_studio_total": 74.65013689584615,
                    "blank_total": 0.0,
                },
                "name": "main",
                "start": 3,
            }
        ]
    }
