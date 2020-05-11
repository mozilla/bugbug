# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import collections
import concurrent.futures
import itertools
import math
import os
import struct
import traceback
from datetime import datetime
from logging import INFO, basicConfig, getLogger
from typing import Generator, List, NewType, Tuple

import adr
import dateutil.parser
import mozci.push
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from bugbug import commit_features, db, repository, test_scheduling
from bugbug.utils import create_tar_zst, zstd_compress

Revision = NewType("Revision", str)
TaskName = NewType("TaskName", str)
PushResult = Tuple[List[Revision], List[TaskName], List[TaskName], List[TaskName]]

basicConfig(level=INFO)
logger = getLogger(__name__)

JOBS_TO_CONSIDER = ("test-", "build-")
JOBS_TO_IGNORE = (
    "build-docker-image-",
    "-android-hw-",
    "-awsy-",
    "-raptor-",
    "-talos-",
    "backlog",
    # inclusive test suites -- these *only* run when certain files have changed
    "-test-verify-",
    "-test-coverage-",
    "jittest",
    "jsreftest",
    "android-hw-gfx",
)

# The mozci version (to bump whenever we change the mozci regression algorithm),
# so we can keep track of which version of mozci was used to analyze a given push
# and we can decide when we want to regenerate parts of the dataset.
MOZCI_VERSION = 2

TRAINING_MONTHS = {
    "label": 7,
    "group": 7,
}


def filter_runnables(runnables, all_runnables, granularity):
    return tuple(
        runnable
        for runnable in runnables
        if runnable in all_runnables
        and (
            granularity == "group"
            or (
                any(runnable.startswith(j) for j in JOBS_TO_CONSIDER)
                and not any(j in runnable for j in JOBS_TO_IGNORE)
            )
        )
    )


# Handle "meaningless" labeling changes ("meaningless" as they shouldn't really affect test scheduling).
def rename_tasks(tasks):
    return [task.replace("test-linux64-", "test-linux1804-64-") for task in tasks]


class Retriever(object):
    def generate_push_data(self, granularity: str) -> None:
        # We keep in the cache the fact that we failed to analyze a push for 10
        # days, so if we re-run often we don't retry the same pushes many times.
        MISSING_CACHE_RETENTION = 10 * 24 * 60

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

        def cache_key(push: mozci.push.Push) -> str:
            return f"push_data.{granularity}.{push.rev}"

        # Regenerating a large amount of data when we update the mozci regression detection
        # algorithm is currently pretty slow, so we only regenerate 1000 pushes whenever we
        # run.
        """to_regenerate = 0
        for push in pushes[::-1]:
            cached = cache[push]
            if not cached:
                continue

            value, mozci_version = cached
            if mozci_version != MOZCI_VERSION and to_regenerate < 1000:
                cache[push] = None
                to_regenerate += 1"""

        def generate() -> Generator[PushResult, None, None]:
            num_cached = 0

            with concurrent.futures.ThreadPoolExecutor() as executor:
                futures = tuple(
                    executor.submit(
                        lambda push: adr.config.cache.get(cache_key(push)), push
                    )
                    for push in pushes
                )

                for push, future in zip(tqdm(pushes), futures):
                    exc = future.exception()
                    if exc is not None:
                        logger.info(f"Exception {exc} while getting {push.rev}")
                        for f in futures:
                            f.cancel()

                    cached = future.result()

                    # Regenerate results which were generated when we were not cleaning
                    # up WPT groups.
                    if cached:
                        value, mozci_version = cached
                        if any(runnable.startswith("/") for runnable in value[1]):
                            cached = None

                    if cached is not None:
                        num_cached += 1
                        if cached:
                            value, mozci_version = cached
                            yield value
                    else:
                        logger.info(
                            f"Analyzing {push.rev} at the {granularity} level..."
                        )

                        key = cache_key(push)

                        try:
                            if granularity == "label":
                                runnables = push.task_labels
                            elif granularity == "group":
                                runnables = push.group_summaries.keys()

                            value = (
                                push.revs,
                                list(runnables),
                                list(push.get_possible_regressions(granularity)),
                                list(push.get_likely_regressions(granularity)),
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
                            adr.config.cache.put(key, (), MISSING_CACHE_RETENTION)
                        except Exception:
                            traceback.print_exc()
                            adr.config.cache.put(key, (), MISSING_CACHE_RETENTION)

                logger.info(
                    f"{num_cached} pushes were already cached out of {len(pushes)}"
                )

        db.write(push_data_db, generate())
        zstd_compress(push_data_db)

    def retrieve_push_data(self) -> None:
        self.generate_push_data("label")
        self.generate_push_data("group")

    def generate_test_scheduling_history(self, granularity):
        # Get the commits DB.
        assert db.download(repository.COMMITS_DB)

        HISTORY_DATE_START = datetime.now() - relativedelta(
            months=TRAINING_MONTHS[granularity]
        )

        if granularity == "label":
            push_data_db = test_scheduling.PUSH_DATA_LABEL_DB
            test_scheduling_db = test_scheduling.TEST_LABEL_SCHEDULING_DB
            past_failures_db = os.path.join(
                "data", test_scheduling.PAST_FAILURES_LABEL_DB
            )
            failing_together_db = os.path.join(
                "data", test_scheduling.FAILING_TOGETHER_LABEL_DB
            )
        elif granularity == "group":
            push_data_db = test_scheduling.PUSH_DATA_GROUP_DB
            test_scheduling_db = test_scheduling.TEST_GROUP_SCHEDULING_DB
            past_failures_db = os.path.join(
                "data", test_scheduling.PAST_FAILURES_GROUP_DB
            )
            touched_together_db = os.path.join(
                "data", test_scheduling.TOUCHED_TOGETHER_DB
            )

        assert db.download(push_data_db)

        db.download(test_scheduling_db, support_files_too=True)

        last_node = None
        for revs, _ in test_scheduling.get_test_scheduling_history(granularity):
            last_node = revs[0]

        def generate_failing_together_probabilities(push_data):
            # TODO: we should consider the probabilities of `task1 failure -> task2 failure` and
            # `task2 failure -> task1 failure` separately, as they could be different.

            count_runs = collections.Counter()
            count_single_failures = collections.Counter()
            count_both_failures = collections.Counter()

            for revisions, tasks, likely_regressions, candidate_regressions in tqdm(
                push_data
            ):
                failures = set(likely_regressions + candidate_regressions)
                all_tasks = list(set(tasks) | failures)

                for task1, task2 in itertools.combinations(sorted(all_tasks), 2):
                    count_runs[(task1, task2)] += 1

                    if task1 in failures:
                        if task2 in failures:
                            count_both_failures[(task1, task2)] += 1
                        else:
                            count_single_failures[(task1, task2)] += 1
                    elif task2 in failures:
                        count_single_failures[(task1, task2)] += 1

            stats = {}

            skipped = 0

            for couple, run_count in count_runs.most_common():
                failure_count = count_both_failures[couple]
                support = failure_count / run_count

                if support < 1 / 700:
                    skipped += 1
                    continue

                if failure_count != 0:
                    confidence = failure_count / (
                        count_single_failures[couple] + failure_count
                    )
                else:
                    confidence = 0.0

                stats[couple] = (support, confidence)

            logger.info(f"{skipped} couples skipped because their support was too low")

            logger.info("Redundancies with the highest support and confidence:")
            for couple, (support, confidence) in sorted(
                stats.items(), key=lambda k: (-k[1][1], -k[1][0])
            )[:7]:
                failure_count = count_both_failures[couple]
                run_count = count_runs[couple]
                logger.info(
                    f"{couple[0]} - {couple[1]} redundancy confidence {confidence}, support {support} ({failure_count} over {run_count})."
                )

            logger.info("Redundancies with the highest confidence and lowest support:")
            for couple, (support, confidence) in sorted(
                stats.items(), key=lambda k: (-k[1][1], k[1][0])
            )[:7]:
                failure_count = count_both_failures[couple]
                run_count = count_runs[couple]
                logger.info(
                    f"{couple[0]} - {couple[1]} redundancy confidence {confidence}, support {support} ({failure_count} over {run_count})."
                )

            failing_together = test_scheduling.get_failing_together_db()
            count_redundancies = collections.Counter()
            for couple, (support, confidence) in stats.items():
                if confidence == 1.0:
                    count_redundancies["==100%"] += 1
                if confidence > 0.9:
                    count_redundancies[">=90%"] += 1
                if confidence > 0.8:
                    count_redundancies[">=80%"] += 1
                if confidence > 0.7:
                    count_redundancies[">=70%"] += 1

                if confidence < 0.7:
                    continue

                failing_together[
                    f"{couple[0]}${couple[1]}".encode("utf-8")
                ] = struct.pack("ff", support, confidence)

            for percentage, count in count_redundancies.most_common():
                logger.info(f"{count} with {percentage} confidence")

            test_scheduling.close_failing_together_db()

        def generate_all_data():
            past_failures = test_scheduling.get_past_failures(granularity)

            push_num = past_failures["push_num"] if "push_num" in past_failures else 0

            # We can start once we get to the last revision we added in the previous run.
            can_start = True if last_node is None else False

            commit_map = {}
            for commit_data in tqdm(repository.get_commits()):
                if not can_start:
                    if last_node == commit_data["node"]:
                        can_start = True

                    continue

                commit_map[commit_data["node"]] = commit_data

            push_data = list(db.read(push_data_db))

            logger.info(f"push data nodes: {len(push_data)}")

            if granularity == "label":
                push_data = [
                    (
                        revisions,
                        rename_tasks(push_tasks),
                        rename_tasks(possible_regressions),
                        rename_tasks(likely_regressions),
                    )
                    for revisions, push_tasks, possible_regressions, likely_regressions in push_data
                ]

            # In the last 14 pushes, we definitely run all possible runnables.
            all_runnables_set = set(
                sum((push_runnables for _, push_runnables, _, _ in push_data[-14:]), [])
            )
            # Filter runnables we don't need.
            all_runnables = filter_runnables(
                list(all_runnables_set), all_runnables_set, granularity
            )
            all_runnables_set = set(all_runnables_set)
            logger.info(f"{len(all_runnables_set)} runnables run in the last 14 pushes")

            push_data = [
                (
                    revisions,
                    filter_runnables(push_tasks, all_runnables_set, granularity),
                    filter_runnables(
                        possible_regressions, all_runnables_set, granularity
                    ),
                    filter_runnables(
                        likely_regressions, all_runnables_set, granularity
                    ),
                )
                for revisions, push_tasks, possible_regressions, likely_regressions in push_data
            ]

            if granularity == "label":
                generate_failing_together_probabilities(push_data)

            # Store all runnables in the past_failures DB so it can be used in the evaluation phase.
            past_failures["all_runnables"] = all_runnables
            # XXX: Should we recreate the DB from scratch if the previous all_runnables are not the
            # same as the current ones?

            saved_nodes = set()
            skipped_no_commits = 0
            skipped_too_big_commits = 0
            skipped_no_runnables = 0

            # We can start once we get to the last revision we added in the previous run.
            can_start = True if last_node is None else False

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

                if not can_start:
                    if last_node == revisions[0]:
                        can_start = True

                    continue

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

                result = {
                    "revs": revisions,
                    "data": [],
                }
                for data in test_scheduling.generate_data(
                    past_failures,
                    merged_commits,
                    push_num,
                    runnables_to_consider,
                    possible_regressions,
                    likely_regressions,
                ):
                    if pushdate > HISTORY_DATE_START:
                        result["data"].append(data)

                if pushdate > HISTORY_DATE_START:
                    saved_nodes.add(i)
                    yield result

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
