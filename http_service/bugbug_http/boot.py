# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import tempfile

from bugbug import db, repository, test_scheduling


def boot_worker():

    # Clone mozilla central
    repo_dir = os.path.join(tempfile.gettempdir(), "bugbug-hg")
    repository.clone(repo_dir)

    # Download test scheduling DB
    assert db.download_support_file(
        test_scheduling.TEST_SCHEDULING_DB, test_scheduling.PAST_FAILURES_DB
    )

    # Download commits DB
    assert db.download(repository.COMMITS_DB, support_files_too=True)

    # And update it
    for commit in repository.get_commits():
        pass

    rev_start = "children({})".format(commit["node"])
    repository.download_commits(repo_dir, rev_start)
