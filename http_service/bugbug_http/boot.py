# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import concurrent.futures
import contextlib
import logging
import os

import hglib
import requests
import tenacity
from tqdm import tqdm

from bugbug import repository, test_scheduling, utils
from bugbug_http import ALLOW_MISSING_MODELS, REPO_DIR

logger = logging.getLogger(__name__)

# The number of recent autoland pushes whose target tasks are the known tasks. They include backstop
# pushes, which target all tasks (one every 20 pushes or 4 hours).
AUTOLAND_PUSHES = 50


def boot_worker() -> None:
    # Clone autoland
    def clone_autoland() -> None:
        logger.info("Cloning autoland in %s...", REPO_DIR)
        repository.clone(
            REPO_DIR, "https://hg.mozilla.org/integration/autoland", update=True
        )

    def extract_past_failures_label() -> None:
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

    def extract_failing_together_label() -> None:
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.FAILING_TOGETHER_LABEL_DB)
            )
            logger.info("Failing together label DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Failing together label DB not extracted, but missing models are allowed."
            )

    def extract_failing_together_config_group() -> None:
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.FAILING_TOGETHER_CONFIG_GROUP_DB)
            )
            logger.info("Failing together config/group DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Failing together config/group DB not extracted, but missing models are allowed."
            )

    def extract_past_failures_group() -> None:
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

    def extract_touched_together() -> None:
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

    def extract_commits() -> bool:
        try:
            utils.extract_file(f"{repository.COMMITS_DB}.zst")
            logger.info("Commits DB extracted.")
            return True
        except FileNotFoundError:
            logger.info("Commits DB not extracted, but missing models are allowed.")
            assert ALLOW_MISSING_MODELS
            return False

    def extract_commit_experiences() -> None:
        try:
            utils.extract_file(os.path.join("data", repository.COMMIT_EXPERIENCES_DB))
            logger.info("Commit experiences DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Commit experiences DB not extracted, but missing models are allowed."
            )

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(7),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=8),
    )
    def retrieve_schedulable_tasks() -> None:
        # The pushes of the last week (many more than AUTOLAND_PUSHES, also during holidays).
        r = requests.get(
            "https://hg.mozilla.org/integration/autoland/json-pushes",
            params={"version": "2", "tipsonly": "1", "startdate": "1 week ago"},
        )
        r.raise_for_status()
        pushes = sorted(r.json()["pushes"].items(), key=lambda push: int(push[0]))
        revs = [push_obj["changesets"][0] for _, push_obj in pushes[-AUTOLAND_PUSHES:]]

        logger.info("Retrieving known tasks from %s...", revs)

        # Store in a file the list of tasks in the latest autoland pushes. Pushes don't all target
        # the same tasks (only some of them target almost all), so we use the union of the target
        # tasks of many pushes, which also protects us from broken decision tasks.
        known_tasks = set()
        for rev in revs:
            r = requests.get(
                f"https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.autoland.revision.{rev}.taskgraph.decision/artifacts/public/target-tasks.json"
            )
            if r.ok:
                known_tasks.update(r.json())

        logger.info("Retrieved %d tasks", len(known_tasks))

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
        extract_failing_together_label()
        extract_failing_together_config_group()

        if commits_db_extracted:
            # Update the commits DB.
            logger.info("Browsing all commits...")
            nodes = collections.deque(
                (commit["node"] for commit in tqdm(repository.get_commits())),
                maxlen=4096,
            )
            nodes.reverse()
            logger.info("All commits browsed.")

            # Wait repository to be cloned, as it's required to call repository.download_commits.
            logger.info("Waiting autoland to be cloned...")
            clone_autoland_future.result()

            with hglib.open(REPO_DIR) as hg:
                # Try using nodes backwards, in case we have some node that was on central at the time
                # we mined commits, but is not yet on autoland.
                for node in nodes:
                    try:
                        revs = repository.get_revs(hg, rev_start=f"children({node})")
                        break
                    except hglib.error.CommandError as e:
                        if b"abort: unknown revision" not in e.err:
                            raise

            logger.info("Updating commits DB...")
            try:
                commits = repository.download_commits(REPO_DIR, revs=revs)
                logger.info("Commits DB updated.")

                logger.info("Updating touched together DB...")
                if len(commits) > 0:
                    # Update the touched together DB.
                    update_touched_together_gen = (
                        test_scheduling.update_touched_together()
                    )
                    next(update_touched_together_gen)

                    update_touched_together_gen.send(commits[-1]["node"])

                    with contextlib.suppress(StopIteration):
                        update_touched_together_gen.send(None)

                logger.info("Touched together DB updated.")
            except Exception as e:
                # It's not ideal, but better not to crash the service!
                logger.error("Exception while updating commits DB: %s", e)

        # Wait list of schedulable tasks to be downloaded and written to disk.
        retrieve_schedulable_tasks_future.result()

    logger.info("Worker boot done")
