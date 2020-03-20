# -*- coding: utf-8 -*-

import argparse
import json
import math
import os
import time
import traceback
from datetime import datetime
from logging import INFO, basicConfig, getLogger

import adr
import dateutil.parser
import mozci.push
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from bugbug import commit_features, db, repository, test_scheduling
from bugbug.utils import (
    download_check_etag,
    open_tar_zst,
    zstd_compress,
    zstd_decompress,
)

basicConfig(level=INFO)
logger = getLogger(__name__)

JOBS_TO_CONSIDER = ("test-", "build-")
JOBS_TO_IGNORE = ("build-docker-image-",)

ADR_CACHE_DB = "data/adr_cache.tar"
db.register(
    ADR_CACHE_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/adr_cache.tar.zst",
    3,
)
PUSH_DATA_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_scheduling_history_push_data.latest/artifacts/public/push_data_{granularity}.json.zst"

TRAINING_MONTHS = {
    "label": 7,
    "group": 5,
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
                and not any(runnable.startswith(j) for j in JOBS_TO_IGNORE)
            )
        )
    )


# Handle "meaningless" labeling changes ("meaningless" as they shouldn't really affect test scheduling).
def rename_tasks(tasks):
    return [task.replace("test-linux64-", "test-linux1804-64-") for task in tasks]


class Retriever(object):
    def __init__(self):
        os.makedirs("data", exist_ok=True)

    def generate_push_data(self, runnable):
        def upload_adr_cache():
            cache_path = os.path.splitext(ADR_CACHE_DB)[0]
            assert os.path.abspath(
                adr.config["cache"]["stores"]["file"]["path"]
            ) == os.path.abspath(cache_path)

            with open_tar_zst(f"{ADR_CACHE_DB}.zst") as tar:
                tar.add(cache_path)

            db.upload(ADR_CACHE_DB)

        # We keep in the cache the fact that we failed to analyze a push for 10
        # days, so if we re-run often we don't retry the same pushes many times.
        MISSING_CACHE_RETENTION = 10 * 24 * 60

        # We'll use the past TRAINING_MONTHS months only for training the model,
        # but we use half TRAINING_MONTHS months more than that to calculate the
        # failure statistics.
        from_months = TRAINING_MONTHS[runnable] + math.floor(
            TRAINING_MONTHS[runnable] / 2
        )

        pushes = mozci.push.make_push_objects(
            from_date=f"today-{from_months}month",
            to_date="today-3day",
            branch="autoland",
        )

        start_time = time.monotonic()

        num_cached = 0

        push_data = []

        for push in tqdm(pushes):
            key = f"push_data.{runnable}.{push.rev}"

            logger.info(f"Analyzing {push.rev} at the {runnable} level...")

            if adr.config.cache.has(key):
                num_cached += 1
                value = adr.config.cache.get(key)
                if value is not None:
                    push_data.append(value)
            else:
                try:
                    if runnable == "label":
                        runnables = push.task_labels
                    elif runnable == "group":
                        runnables = push.group_summaries.keys()

                    value = [
                        push.revs,
                        list(runnables),
                        list(push.get_possible_regressions(runnable)),
                        list(push.get_likely_regressions(runnable)),
                    ]
                    push_data.append(value)
                    adr.config.cache.forever(key, value)
                except adr.errors.MissingDataError:
                    logger.warning(
                        f"Tasks for push {push.rev} can't be found on ActiveData"
                    )
                    adr.config.cache.put(key, None, MISSING_CACHE_RETENTION)
                except Exception:
                    traceback.print_exc()
                    adr.config.cache.put(key, None, MISSING_CACHE_RETENTION)

            if time.monotonic() - start_time >= 3600:
                upload_adr_cache()
                start_time = time.monotonic()

        logger.info(f"{num_cached} pushes were already cached out of {len(pushes)}")

        upload_adr_cache()

        with open(f"push_data_{runnable}.json", "w") as f:
            json.dump(push_data, f)

        zstd_compress(f"push_data_{runnable}.json")

    def retrieve_push_data(self):
        # Download previous cache.
        db.download(ADR_CACHE_DB)
        self.generate_push_data("label")
        self.generate_push_data("group")

    def generate_test_scheduling_history(self, granularity):
        push_data_path = f"push_data_{granularity}.json"
        updated = download_check_etag(PUSH_DATA_URL.format(granularity=granularity))
        if updated:
            zstd_decompress(push_data_path)
        assert os.path.exists(push_data_path), "Decompressed push data file exists"

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
        elif granularity == "group":
            test_scheduling_db = test_scheduling.TEST_GROUP_SCHEDULING_DB
            past_failures_db = os.path.join(
                "data", test_scheduling.PAST_FAILURES_GROUP_DB
            )
            touched_together_db = os.path.join(
                "data", test_scheduling.TOUCHED_TOGETHER_DB
            )

        db.download(test_scheduling_db, support_files_too=True)

        last_node = None
        for test_data in test_scheduling.get_test_scheduling_history(granularity):
            last_node = test_data["revs"][0]

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

            with open(push_data_path, "r") as f:
                push_data = json.load(f)

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

            # In the last 28 pushes, we definitely run all possible runnables.
            all_runnables_set = set(
                sum((push_runnables for _, push_runnables, _, _ in push_data[-28:]), [])
            )
            # Filter runnables we don't need.
            all_runnables = filter_runnables(
                list(all_runnables_set), all_runnables_set, granularity
            )
            all_runnables_set = set(all_runnables_set)
            logger.info(f"{len(all_runnables_set)} runnables run in the last 28 pushes")

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
                # So we consider only the runnables which run in this push, and the possible and likely regressions
                # from this push.
                runnables_to_consider = list(
                    set(push_runnables + possible_regressions + likely_regressions)
                )
                runnables_to_consider = filter_runnables(
                    runnables_to_consider, all_runnables_set, granularity
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

                for data in test_scheduling.generate_data(
                    past_failures,
                    merged_commits,
                    push_num,
                    runnables_to_consider,
                    possible_regressions,
                    likely_regressions,
                ):
                    if pushdate > HISTORY_DATE_START:
                        saved_nodes.add(i)
                        data["revs"] = revisions
                        yield data

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

        with open_tar_zst(past_failures_db) as tar:
            tar.add(past_failures_db[: -len(".tar.zst")])

        if granularity == "group":
            with open_tar_zst(touched_together_db) as tar:
                tar.add(touched_together_db[: -len(".tar.zst")])


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
