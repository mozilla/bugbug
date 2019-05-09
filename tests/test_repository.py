# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import shutil

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
