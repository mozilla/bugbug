# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import math
import os
import threading
import traceback
from datetime import datetime
from logging import INFO, basicConfig, getLogger
from typing import Any, Dict, Generator, List

import adr
import dateutil.parser
import mozci.push
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from bugbug import commit_features, db, repository, test_scheduling
from bugbug.test_scheduling import PushResult
from bugbug.utils import create_tar_zst, zstd_compress

basicConfig(level=INFO)
logger = getLogger(__name__)

# The mozci version (to bump whenever we change the mozci regression algorithm),
# so we can keep track of which version of mozci was used to analyze a given push
# and we can decide when we want to regenerate parts of the dataset.
MOZCI_VERSION = 2

TRAINING_MONTHS = {
    "label": 7,
    "group": 7,
    "config_group": 1,
}


class Retriever(object):
    def generate_push_data(self, granularity: str) -> None:
        # We'll use the past TRAINING_MONTHS months only for training the model,
        # but we use half TRAINING_MONTHS months more than that to calculate the
        # failure statistics.
        from_months = TRAINING_MONTHS[granularity] + math.floor(
            TRAINING_MONTHS[granularity] / 2
        )

        # We use the actual date instead of 'today-X' aliases to avoid adr caching
        # this query.
        from_date = datetime.utcnow() - relativedelta(months=from_months)
        to_date = datetime.utcnow() - relativedelta(days=3)

        pushes = mozci.push.make_push_objects(
            from_date=from_date.strftime("%Y-%m-%d"),
            to_date=to_date.strftime("%Y-%m-%d"),
            branch="autoland",
        )

        if granularity == "label":
            push_data_db = test_scheduling.PUSH_DATA_LABEL_DB
        elif granularity == "group":
            push_data_db = test_scheduling.PUSH_DATA_GROUP_DB
        elif granularity == "config_group":
            push_data_db = test_scheduling.PUSH_DATA_CONFIG_GROUP_DB

        def cache_key(push: mozci.push.Push) -> str:
            return f"push_data.{granularity}.{push.rev}"

        def generate(
            futures: List[concurrent.futures.Future],
        ) -> Generator[PushResult, None, None]:
            num_cached = 0
            num_pushes = len(pushes)

            # Regenerating a large amount of data when we update the mozci regression detection
            # algorithm is currently pretty slow, so we only regenerate 1000 pushes whenever we
            # run.
            to_regenerate = 1000

            for i in tqdm(range(len(futures))):
                cached = futures.pop(0).result()
                push = pushes.pop(0)

                semaphore.release()

                if cached and to_regenerate > 0:
                    value, mozci_version = cached

                    # Regenerate results which were generated when we were not cleaning
                    # up WPT groups.
                    if granularity == "group" and any(
                        runnable.startswith("/") for runnable in value[1]
                    ):
                        cached = None
                        to_regenerate -= 1

                    """# Regenerate results which were generated with an older version of mozci.
                    elif mozci_version != MOZCI_VERSION and to_regenerate > 0:
                        cached = None
                        to_regenerate -= 1"""

                if cached:
                    num_cached += 1
                    value, mozci_version = cached
                    yield value
                else:
                    logger.info(f"Analyzing {push.rev} at the {granularity} level...")

                    key = cache_key(push)

                    try:
                        if granularity == "label":
                            runnables = push.task_labels
                        elif granularity == "group":
                            runnables = push.group_summaries.keys()
                        elif granularity == "config_group":
                            runnables = push.config_group_summaries.keys()

                        value = (
                            push.revs,
                            tuple(runnables),
                            tuple(push.get_possible_regressions(granularity)),
                            tuple(push.get_likely_regressions(granularity)),
                        )
                        adr.config.cache.put(
                            key,
                            (value, MOZCI_VERSION),
                            adr.config["cache"]["retention"],
                        )
                        yield value
                    except adr.errors.MissingDataError:
                        logger.warning(
                            f"Tasks for push {push.rev} can't be found on ActiveData"
                        )
                    except Exception:
                        traceback.print_exc()

            logger.info(f"{num_cached} pushes were already cached out of {num_pushes}")

        semaphore = threading.BoundedSemaphore(256)

        def retrieve_from_cache(push):
            semaphore.acquire()
            return adr.config.cache.get(cache_key(push))

        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(retrieve_from_cache, push) for push in pushes]

            try:
                db.write(push_data_db, generate(futures))
            except Exception:
                for f in futures:
                    f.cancel()

                    try:
                        semaphore.release()
                    except ValueError:
                        continue

                raise

        zstd_compress(push_data_db)

    def retrieve_push_data(self) -> None:
        self.generate_push_data("config_group")
        self.generate_push_data("label")
        self.generate_push_data("group")

    def generate_test_scheduling_history(self, granularity: str) -> None:
        # Get the commits DB.
        assert db.download(repository.COMMITS_DB)

        HISTORY_DATE_START = datetime.now() - relativedelta(
            months=TRAINING_MONTHS[granularity]
        )

        if granularity == "label":
            test_scheduling_db = test_scheduling.TEST_LABEL_SCHEDULING_DB
            past_failures_db = os.path.join(
                "data", test_scheduling.PAST_FAILURES_LABEL_DB
            )
            failing_together_db = os.path.join(
                "data", test_scheduling.FAILING_TOGETHER_LABEL_DB
            )
        elif granularity == "group":
            test_scheduling_db = test_scheduling.TEST_GROUP_SCHEDULING_DB
            past_failures_db = os.path.join(
                "data", test_scheduling.PAST_FAILURES_GROUP_DB
            )
            touched_together_db = os.path.join(
                "data", test_scheduling.TOUCHED_TOGETHER_DB
            )

        push_data, all_runnables = test_scheduling.get_push_data(granularity)

        def generate_all_data() -> Generator[Dict[str, Any], None, None]:
            past_failures = test_scheduling.get_past_failures(granularity)

            push_num = past_failures["push_num"] if "push_num" in past_failures else 0

            commit_map = {}
            for commit_data in tqdm(repository.get_commits()):
                commit_map[commit_data["node"]] = commit_data

            if granularity == "label":
                test_scheduling.generate_failing_together_probabilities(push_data)

            # Store all runnables in the past_failures DB so it can be used in the evaluation phase.
            past_failures["all_runnables"] = all_runnables
            # XXX: Should we recreate the DB from scratch if the previous all_runnables are not the
            # same as the current ones?

            saved_nodes = set()
            skipped_no_commits = 0
            skipped_too_big_commits = 0
            skipped_no_runnables = 0

            if granularity == "group":
                update_touched_together_gen = test_scheduling.update_touched_together()
                next(update_touched_together_gen)

            for i in tqdm(range(len(push_data))):
                (
                    revisions,
                    push_runnables,
                    possible_regressions,
                    likely_regressions,
                ) = push_data.pop(0)

                push_num += 1

                # XXX: Some commits are skipped in the repository mining, e.g. merges and backouts. Maybe we should not skip them.
                commits = tuple(
                    commit_map.pop(revision)
                    for revision in revisions
                    if revision in commit_map
                )
                if len(commits) == 0:
                    skipped_no_commits += 1
                    continue

                merged_commits = commit_features.merge_commits(commits)

                # XXX: For now, skip commits which are too large.
                # In the future we can either:
                #  - Improve shelve perf and go back to consider all files;
                #  - Consider only files which appear with a given frequency, like the "files" feature in commit_features;
                #  - Keep a limit of number of files.
                if len(merged_commits["files"]) > 50:
                    skipped_too_big_commits += 1
                    continue

                # If we considered all_runnables, we'd generate a huge amount of data.
                # We consider only the runnables which run in this push, and the possible and likely regressions
                # from this push. We can't consider all runnables because we can't be sure that a task that didn't
                # run on a push would have been successful.
                runnables_to_consider = list(
                    set(push_runnables + possible_regressions + likely_regressions)
                )

                if len(runnables_to_consider) == 0:
                    skipped_no_runnables += 1
                    continue

                # Sync DB every 250 pushes, so we cleanup the shelve cache (we'd run OOM otherwise!).
                if i % 250 == 0:
                    past_failures.sync()

                pushdate = dateutil.parser.parse(merged_commits["pushdate"])

                if granularity == "group":
                    update_touched_together_gen.send(commits[0]["node"])

                result_data = []
                for data in test_scheduling.generate_data(
                    past_failures,
                    merged_commits,
                    push_num,
                    runnables_to_consider,
                    possible_regressions,
                    likely_regressions,
                ):
                    if pushdate > HISTORY_DATE_START:
                        result_data.append(data)

                if pushdate > HISTORY_DATE_START:
                    saved_nodes.add(i)
                    yield {
                        "revs": revisions,
                        "data": result_data,
                    }

            if granularity == "group":
                try:
                    update_touched_together_gen.send(None)
                except StopIteration:
                    pass

            logger.info(f"saved push data nodes: {len(saved_nodes)}")
            logger.info(f"skipped {skipped_no_commits} (no commits in our DB)")
            logger.info(f"skipped {skipped_too_big_commits} (too big commits)")
            logger.info(f"skipped {skipped_no_runnables} (no interesting runnables)")

            past_failures["push_num"] = push_num
            past_failures.close()

        db.append(test_scheduling_db, generate_all_data())

        zstd_compress(test_scheduling_db)
        create_tar_zst(past_failures_db)

        if granularity == "group":
            create_tar_zst(touched_together_db)

        if granularity == "label":
            create_tar_zst(failing_together_db)


def main():
    description = "Retrieve and extract the test scheduling history from ActiveData"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument(
        "op", help="Which operation to perform.", choices=["retrieve", "generate"]
    )
    parser.add_argument(
        "--granularity",
        help="Which test granularity to use.",
        choices=["label", "group"],
    )

    args = parser.parse_args()

    retriever = Retriever()
    if args.op == "retrieve":
        retriever.retrieve_push_data()
    elif args.op == "generate":
        assert args.granularity is not None
        retriever.generate_test_scheduling_history(args.granularity)


if __name__ == "__main__":
    main()
