# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import concurrent.futures
import logging

import bugbug_http.models
from bugbug import db, repository, test_scheduling
from bugbug_http import ALLOW_MISSING_MODELS, REPO_DIR

logger = logging.getLogger(__name__)


def boot_worker():
    # Clone autoland
    def clone_autoland():
        logger.info(f"Cloning autoland in {REPO_DIR}...")
        repository.clone(REPO_DIR, "https://hg.mozilla.org/integration/autoland")

    # Download test scheduling DB support files.
    def download_past_failures_label():
        assert (
            db.download_support_file(
                test_scheduling.TEST_LABEL_SCHEDULING_DB,
                test_scheduling.PAST_FAILURES_LABEL_DB,
            )
            or ALLOW_MISSING_MODELS
        )
        logger.info("Label-level past failures DB downloaded.")

    def download_past_failures_group():
        assert (
            db.download_support_file(
                test_scheduling.TEST_GROUP_SCHEDULING_DB,
                test_scheduling.PAST_FAILURES_GROUP_DB,
            )
            or ALLOW_MISSING_MODELS
        )
        logger.info("Group-level past failures DB downloaded.")

    def download_touched_together():
        assert (
            db.download_support_file(
                test_scheduling.TEST_GROUP_SCHEDULING_DB,
                test_scheduling.TOUCHED_TOGETHER_DB,
            )
            or ALLOW_MISSING_MODELS
        )
        logger.info("Touched together DB downloaded.")

    def download_commits():
        commits_db_downloaded = db.download(repository.COMMITS_DB)
        if not ALLOW_MISSING_MODELS:
            assert commits_db_downloaded
        logger.info("Commits DB downloaded.")
        return commits_db_downloaded

    def download_commit_experiences():
        logger.info("download_commit_experiences")
        assert (
            db.download_support_file(
                repository.COMMITS_DB, repository.COMMIT_EXPERIENCES_DB,
            )
            or ALLOW_MISSING_MODELS
        )
        logger.info("Commit experiences DB downloaded.")

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []

        download_commits_future = executor.submit(download_commits)
        futures.append(download_commits_future)
        download_commit_experiences_future = executor.submit(
            download_commit_experiences
        )
        futures.append(download_commit_experiences_future)

        clone_autoland_future = executor.submit(clone_autoland)
        futures.append(clone_autoland_future)

        download_touched_together_future = executor.submit(download_touched_together)
        futures.append(download_touched_together_future)

        futures.append(executor.submit(download_past_failures_label))

        futures.append(executor.submit(download_past_failures_group))

        commits_db_downloaded = download_commits_future.result()
        if commits_db_downloaded:
            # Update the commits DB.
            logger.info("Browsing all commits...")
            for commit in repository.get_commits():
                pass
            logger.info("All commits browsed.")

            # Wait commit experiences DB to be downloaded, as it's required to call
            # repository.download_commits.
            logger.info("Waiting commit experiences DB to be downloaded...")
            download_commit_experiences_future.result()

            # Wait repository to be cloned, as it's required to call repository.download_commits.
            logger.info("Waiting autoland to be cloned...")
            clone_autoland_future.result()

            rev_start = "children({})".format(commit["node"])
            logger.info("Updating commits DB...")
            commits = repository.download_commits(
                REPO_DIR, rev_start, use_single_process=True
            )
            logger.info("Commits DB updated.")

            # Wait touched together DB to be downloaded.
            logger.info("Waiting touched together DB to be downloaded...")
            download_touched_together_future.result()

            logger.info("Updating touched together DB...")
            if len(commits) > 0:
                # Update the touched together DB.
                update_touched_together_gen = test_scheduling.update_touched_together()
                next(update_touched_together_gen)

                update_touched_together_gen.send(commits[-1]["node"])

                try:
                    update_touched_together_gen.send(None)
                except StopIteration:
                    pass
            logger.info("Touched together DB updated.")

        # Make sure all downloads complete successfully.
        for future in concurrent.futures.as_completed(futures):
            future.result()

    # Preload models
    bugbug_http.models.preload_models()

    logger.info("Worker boot done")
