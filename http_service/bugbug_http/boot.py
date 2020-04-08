# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import concurrent.futures
import logging
import os

from bugbug import repository, test_scheduling, utils
from bugbug_http import ALLOW_MISSING_MODELS, REPO_DIR

logger = logging.getLogger(__name__)


def boot_worker():
    # Clone autoland
    def clone_autoland():
        logger.info(f"Cloning autoland in {REPO_DIR}...")
        repository.clone(REPO_DIR, "https://hg.mozilla.org/integration/autoland")

    def extract_past_failures_label():
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.PAST_FAILURES_LABEL_DB)
            )
            logger.info("Label-level past failures DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Label-level past failures DB not extracted, but missing models are allowed."
            )

    def extract_failing_together():
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.FAILING_TOGETHER_LABEL_DB)
            )
            logger.info("Failing together DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Failing together DB not extracted, but missing models are allowed."
            )

    def extract_past_failures_group():
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.PAST_FAILURES_GROUP_DB)
            )
            logger.info("Group-level past failures DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Group-level past failures DB not extracted, but missing models are allowed."
            )

    def extract_touched_together():
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.TOUCHED_TOGETHER_DB)
            )
            logger.info("Touched together DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Touched together DB not extracted, but missing models are allowed."
            )

    def extract_commits():
        try:
            utils.extract_file(f"{repository.COMMITS_DB}.zst")
            logger.info("Commits DB extracted.")
            return True
        except FileNotFoundError:
            logger.info("Commits DB not extracted, but missing models are allowed.")
            assert ALLOW_MISSING_MODELS
            return False

    def extract_commit_experiences():
        try:
            utils.extract_file(os.path.join("data", repository.COMMIT_EXPERIENCES_DB))
            logger.info("Commit experiences DB extracted.")
        except FileNotFoundError:
            logger.info(
                "Commit experiences DB not extracted, but missing models are allowed."
            )
            assert ALLOW_MISSING_MODELS

    with concurrent.futures.ThreadPoolExecutor() as executor:
        clone_autoland_future = executor.submit(clone_autoland)

        commits_db_extracted = extract_commits()
        extract_commit_experiences()
        extract_touched_together()
        extract_past_failures_label()
        extract_past_failures_group()
        extract_failing_together()

        if commits_db_extracted:
            # Update the commits DB.
            logger.info("Browsing all commits...")
            for commit in repository.get_commits():
                pass
            logger.info("All commits browsed.")

            # Wait repository to be cloned, as it's required to call repository.download_commits.
            logger.info("Waiting autoland to be cloned...")
            clone_autoland_future.result()

            rev_start = "children({})".format(commit["node"])
            logger.info("Updating commits DB...")
            commits = repository.download_commits(
                REPO_DIR, rev_start, use_single_process=True
            )
            logger.info("Commits DB updated.")

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

    logger.info("Worker boot done")
