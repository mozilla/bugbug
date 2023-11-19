# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import math
import os
import traceback
from datetime import datetime
from logging import INFO, basicConfig, getLogger
from typing import Any, Generator

import dateutil.parser
import mozci.errors
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
MOZCI_VERSION = 5


class Retriever(object):
    def generate_push_data(
        self, granularity: str, training_months: int, reretrieve: int
    ) -> None:
        # We'll use the past training_months months only for training the model,
        # but we use half training_months months more than that to calculate the
        # failure statistics.
        from_months = training_months + math.floor(training_months / 2)

        # We use the actual date instead of 'today-X' aliases to avoid mozci caching
        # this query.
        from_date = datetime.utcnow() - relativedelta(months=from_months)
        to_date = datetime.utcnow() - relativedelta(days=3)

        if granularity == "label":
            push_data_db = test_scheduling.PUSH_DATA_LABEL_DB
        elif granularity == "group":
            push_data_db = test_scheduling.PUSH_DATA_GROUP_DB
        elif granularity == "config_group":
            push_data_db = test_scheduling.PUSH_DATA_CONFIG_GROUP_DB

        def cache_key(push: mozci.push.Push) -> str:
            return f"push_data.{granularity}.{push.rev}"

        def generate(
            progress_bar: tqdm,
            pushes: list[mozci.push.Push],
            futures: list[concurrent.futures.Future],
        ) -> Generator[PushResult, None, None]:
            nonlocal reretrieve
            num_cached = 0
            num_pushes = len(pushes)
            num_errors = 0

            for push, future in zip(pushes, futures):
                cached = future.result()

                # Regenerating a large amount of data when we update the mozci regression detection
                # algorithm is currently pretty slow, so we only regenerate a subset of pushes whenever we
                # run.
                if cached:
                    value, mozci_version = cached

                    # Regenerate results which were generated with an older version of mozci.
                    if reretrieve > 0 and mozci_version != MOZCI_VERSION:
                        cached = None
                        reretrieve -= 1

                if cached:
                    num_cached += 1
                    value, mozci_version = cached
                    assert len(value) == 5
                    if value != "ERROR":
                        yield value
                    else:
                        num_errors += 1
                else:
                    logger.info(
                        "Analyzing %s at the %s level...", push.rev, granularity
                    )

                    key = cache_key(push)

                    try:
                        if granularity == "label":
                            runnables = push.label_summaries.keys()
                        elif granularity == "group":
                            runnables = push.group_summaries.keys()
                        elif granularity == "config_group":
                            runnables = push.config_group_summaries.keys()

                        value = (
                            tuple(push.revs),
                            push.backedoutby or push.bustage_fixed_by,
                            tuple(runnables),
                            tuple(push.get_possible_regressions(granularity)),
                            tuple(push.get_likely_regressions(granularity)),
                        )
                        mozci.config.cache.put(
                            key,
                            (value, MOZCI_VERSION),
                            mozci.config["cache"]["retention"],
                        )
                        assert len(value) == 5
                        yield value
                    except mozci.errors.MissingDataError:
                        logger.warning(
                            f"Tasks for push {push.rev} can't be found on ActiveData"
                        )
                    except Exception:
                        num_errors += 1
                        traceback.print_exc()
                        mozci.config.cache.put(
                            key,
                            ("ERROR", MOZCI_VERSION),
                            mozci.config["cache"]["retention"],
                        )

                progress_bar.update(1)

            logger.info(
                "%d pushes were already cached out of %d", num_cached, num_pushes
            )
            logger.info("There were errors in %d pushes", num_errors)

        def retrieve_from_cache(push):
            return mozci.config.cache.get(cache_key(push))

        total_pushes = len(
            mozci.push.make_push_objects(
                from_date=from_date.strftime("%Y-%m-%d"),
                to_date=to_date.strftime("%Y-%m-%d"),
                branch="autoland",
            )
        )

        with concurrent.futures.ThreadPoolExecutor() as executor:
            with tqdm(total=total_pushes) as progress_bar:
                # Run in batches of 7 days to avoid running out of memory (given that mozci pushes
                # consume a lot of memory, and they all have references to each other through "parent"
                # and "child" links so they are basically never released while we run this).
                while from_date < to_date:
                    next_from_date = from_date + relativedelta(days=7)
                    if next_from_date > to_date:
                        next_from_date = to_date

                    logger.info(
                        "Retrieving pushes from %s to %s...", from_date, next_from_date
                    )

                    pushes = mozci.push.make_push_objects(
                        from_date=from_date.strftime("%Y-%m-%d"),
                        to_date=next_from_date.strftime("%Y-%m-%d"),
                        branch="autoland",
                    )

                    futures = [
                        executor.submit(retrieve_from_cache, push) for push in pushes
                    ]

                    try:
                        db.append(push_data_db, generate(progress_bar, pushes, futures))
                    except Exception:
                        for f in futures:
                            f.cancel()

                        raise

                    from_date = next_from_date

        zstd_compress(push_data_db)

    def generate_test_scheduling_history(
        self, granularity: str, training_months: int
    ) -> None:
        if granularity != "config_group":
            # Get the commits DB.
            assert db.download(repository.COMMITS_DB)

        HISTORY_DATE_START = datetime.now() - relativedelta(months=training_months)

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
        elif granularity == "config_group":
            test_scheduling_db = test_scheduling.TEST_CONFIG_GROUP_SCHEDULING_DB
            past_failures_db = os.path.join(
                "data", test_scheduling.PAST_FAILURES_CONFIG_GROUP_DB
            )
            failing_together_db = os.path.join(
                "data", test_scheduling.FAILING_TOGETHER_CONFIG_GROUP_DB
            )

        push_data_iter, push_data_count, all_runnables = test_scheduling.get_push_data(
            granularity
        )

        if granularity in ("label", "config_group"):
            test_scheduling.generate_failing_together_probabilities(
                granularity, push_data_iter(), push_data_count
            )

        def generate_all_data() -> Generator[dict[str, Any], None, None]:
            past_failures = test_scheduling.PastFailures(granularity, False)

            try:
                push_num = past_failures.push_num
            except KeyError:
                push_num = 0

            commit_map = {}
            for commit_data in tqdm(repository.get_commits()):
                commit_map[commit_data["node"]] = commit_data

            # Store all runnables in the past_failures DB so it can be used in the evaluation phase.
            past_failures.all_runnables = all_runnables
            # XXX: Should we recreate the DB from scratch if the previous all_runnables are not the
            # same as the current ones?

            saved_nodes = set()
            skipped_no_commits = 0
            skipped_too_big_commits = 0
            skipped_no_runnables = 0

            if granularity in ("group", "config_group"):
                update_touched_together_gen = test_scheduling.update_touched_together()
                next(update_touched_together_gen)

            for (
                i,
                (
                    revisions,
                    fix_revision,
                    push_runnables,
                    possible_regressions,
                    likely_regressions,
                ),
            ) in enumerate(tqdm(push_data_iter(), total=push_data_count)):
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

                # Skip wptsync commits, since they are not like normal pushes made by developers.
                if any(repository.is_wptsync(commit) for commit in commits):
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

                if granularity in ("group", "config_group"):
                    update_touched_together_gen.send(commits[0]["node"])

                result_data = []
                for data in test_scheduling.generate_data(
                    granularity,
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

            logger.info("saved push data nodes: %d", len(saved_nodes))
            logger.info("skipped %d (no commits in our DB)", skipped_no_commits)
            logger.info("skipped %d (too big commits)", skipped_too_big_commits)
            logger.info("skipped %d (no interesting runnables)", skipped_no_runnables)

            past_failures.push_num = push_num
            past_failures.close()

        # For the config/group granularity, we are only interested in the failing together DB.
        if granularity != "config_group":
            db.append(test_scheduling_db, generate_all_data())

            zstd_compress(test_scheduling_db)
            create_tar_zst(past_failures_db)

        if granularity == "group":
            create_tar_zst(touched_together_db)

        if granularity in ("label", "config_group"):
            create_tar_zst(failing_together_db)


def main():
    description = "Retrieve and extract the test scheduling history from ActiveData"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument(
        "op", help="Which operation to perform.", choices=["retrieve", "generate"]
    )
    parser.add_argument(
        "granularity",
        help="Which test granularity to use.",
        choices=["label", "group", "config_group"],
    )
    parser.add_argument(
        "--reretrieve",
        type=int,
        default=0,
        help="How many results to reretrieve.",
    )
    parser.add_argument(
        "--training-months",
        type=int,
        required=True,
        help="How many months of pushes to use for training.",
    )

    args = parser.parse_args()

    retriever = Retriever()
    if args.op == "retrieve":
        retriever.generate_push_data(
            args.granularity, args.training_months, args.reretrieve
        )
    elif args.op == "generate":
        retriever.generate_test_scheduling_history(
            args.granularity, args.training_months
        )


if __name__ == "__main__":
    main()
