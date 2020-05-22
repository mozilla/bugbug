# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import concurrent.futures
import logging
import os

import requests
import tenacity

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

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(7),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=8),
    )
    def retrieve_schedulable_tasks():
        r = requests.get(
            "https://hg.mozilla.org/integration/autoland/json-pushes?version=2&tipsonly=1"
        )
        r.raise_for_status()
        revs = [
            push_obj["changesets"][0]
            for push_id, push_obj in r.json()["pushes"].items()
        ]

        logger.info(f"Retrieving known tasks from {revs}")

        # Store in a file the list of tasks in the latest autoland pushes.
        # We use more than one to protect ourselves from broken decision tasks.
        known_tasks = set()
        for rev in revs:
            r = requests.get(
                f"https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.autoland.revision.{rev}.taskgraph.decision/artifacts/public/target-tasks.json"
            )
            if r.ok:
                known_tasks.update(r.json())

        logger.info(f"Retrieved {len(known_tasks)} tasks")

        assert len(known_tasks) > 0

        with open("known_tasks", "w") as f:
            f.write("\n".join(known_tasks))

    with concurrent.futures.ThreadPoolExecutor() as executor:
        clone_autoland_future = executor.submit(clone_autoland)

        retrieve_schedulable_tasks_future = executor.submit(retrieve_schedulable_tasks)

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

        # Wait list of schedulable tasks to be downloaded and written to disk.
        retrieve_schedulable_tasks_future.result()

    logger.info("Worker boot done")
