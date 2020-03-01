# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
import tempfile

import bugbug_http.models
from bugbug import db, repository, test_scheduling
from bugbug_http import ALLOW_MISSING_MODELS

logger = logging.getLogger(__name__)


def boot_worker():
    # Preload models
    bugbug_http.models.preload_models()

    # Clone mozilla central
    repo_dir = os.environ.get(
        "BUGBUG_REPO_DIR", os.path.join(tempfile.gettempdir(), "bugbug-hg")
    )
    logger.info(f"Cloning mozilla-central in {repo_dir}...")
    repository.clone(repo_dir)

    # Download databases
    logger.info("Downloading test scheduling DB support file...")
    assert (
        db.download_support_file(
            test_scheduling.TEST_LABEL_SCHEDULING_DB,
            test_scheduling.PAST_FAILURES_LABEL_DB,
        )
        or ALLOW_MISSING_MODELS
    )

    # Download commits DB
    logger.info("Downloading commits DB...")
    commits_db_downloaded = db.download(repository.COMMITS_DB, support_files_too=True)
    if not ALLOW_MISSING_MODELS:
        assert commits_db_downloaded

    if commits_db_downloaded:
        # And update it
        logger.info("Browsing all commits...")
        for commit in repository.get_commits():
            pass

        rev_start = "children({})".format(commit["node"])
        logger.info("Updating commits DB...")
        repository.download_commits(repo_dir, rev_start)

    logger.info("Worker boot done")
