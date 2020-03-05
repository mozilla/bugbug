# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import bugbug_http.models
from bugbug import db, repository, test_scheduling
from bugbug_http import ALLOW_MISSING_MODELS, REPO_DIR

logger = logging.getLogger(__name__)


def boot_worker():
    # Clone autoland
    logger.info(f"Cloning mozilla autoland in {REPO_DIR}...")
    repository.clone(REPO_DIR, "https://hg.mozilla.org/integration/autoland")

    # Download test scheduling DB support files.
    logger.info("Downloading test scheduling DB support files...")
    assert (
        db.download_support_file(
            test_scheduling.TEST_LABEL_SCHEDULING_DB,
            test_scheduling.PAST_FAILURES_LABEL_DB,
        )
        or ALLOW_MISSING_MODELS
    )

    assert (
        db.download_support_file(
            test_scheduling.TEST_GROUP_SCHEDULING_DB,
            test_scheduling.PAST_FAILURES_GROUP_DB,
        )
        or ALLOW_MISSING_MODELS
    )

    assert (
        db.download_support_file(
            test_scheduling.TEST_GROUP_SCHEDULING_DB,
            test_scheduling.TOUCHED_TOGETHER_DB,
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
        repository.download_commits(REPO_DIR, rev_start)

    # Preload models
    bugbug_http.models.preload_models()

    logger.info("Worker boot done")
