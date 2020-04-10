# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import math
import random
import statistics
import struct
from functools import reduce
from typing import Set

import numpy as np
import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline
from tqdm import tqdm

from bugbug import (
    commit_features,
    repository,
    test_scheduling,
    test_scheduling_features,
)
from bugbug.model import Model


def get_commit_map():
    commit_map = {}

    for commit in repository.get_commits():
        commit_map[commit["node"]] = commit

    assert len(commit_map) > 0
    return commit_map


class TestSelectModel(Model):
    def __init__(self, lemmatization=False, granularity="label", use_subset=False):
        Model.__init__(self, lemmatization)

        self.granularity = granularity
        # This is useful for development purposes, it avoids using too much memory
        # by using a subset of the dataset (dropping some passing runnables).
        self.use_subset = use_subset

        self.training_dbs = [repository.COMMITS_DB]
        self.eval_dbs[repository.COMMITS_DB] = (
            repository.COMMITS_DB,
            repository.COMMIT_EXPERIENCES_DB,
        )
        if granularity == "label":
            self.training_dbs.append(test_scheduling.TEST_LABEL_SCHEDULING_DB)
            self.eval_dbs[test_scheduling.TEST_LABEL_SCHEDULING_DB] = (
                test_scheduling.PAST_FAILURES_LABEL_DB,
                test_scheduling.FAILING_TOGETHER_LABEL_DB,
            )
        elif granularity == "group":
            self.training_dbs.append(test_scheduling.TEST_GROUP_SCHEDULING_DB)
            self.eval_dbs[test_scheduling.TEST_GROUP_SCHEDULING_DB] = (
                test_scheduling.PAST_FAILURES_GROUP_DB,
                test_scheduling.TOUCHED_TOGETHER_DB,
            )

        self.cross_validation_enabled = False

        self.entire_dataset_training = True

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
            test_scheduling_features.prev_failures(),
        ]

        if granularity == "label":
            feature_extractors += [
                test_scheduling_features.platform(),
                # test_scheduling_features.chunk(),
                test_scheduling_features.suite(),
            ]
        elif granularity == "group":
            feature_extractors += [
                commit_features.source_code_files_modified_num(),
                commit_features.other_files_modified_num(),
                commit_features.test_files_modified_num(),
                commit_features.source_code_file_size(),
                commit_features.other_file_size(),
                commit_features.test_file_size(),
                commit_features.source_code_added(),
                commit_features.other_added(),
                commit_features.test_added(),
                commit_features.source_code_deleted(),
                commit_features.other_deleted(),
                commit_features.test_deleted(),
                test_scheduling_features.path_distance(),
                test_scheduling_features.common_path_components(),
                test_scheduling_features.touched_together(),
            ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "commit_extractor",
                    commit_features.CommitExtractor(feature_extractors, []),
                ),
                ("union", ColumnTransformer([("data", DictVectorizer(), "data")])),
            ]
        )

        self.clf = xgboost.XGBClassifier(n_jobs=16)
        self.clf.set_params(predictor="cpu_predictor")

    def get_pushes(self):
        if self.use_subset:
            random.seed(0)

        pushes = []
        for revs, test_datas in test_scheduling.get_test_scheduling_history(
            self.granularity
        ):
            failures = []
            passes = []

            for test_data in test_datas:
                name = test_data["name"]

                if self.granularity == "label" and not name.startswith("test-"):
                    continue

                if (
                    test_data["is_likely_regression"]
                    or test_data["is_possible_regression"]
                ):
                    failures.append(name)
                else:
                    passes.append(name)

            if self.use_subset:
                passes = random.sample(passes, math.ceil(len(passes) / 10))

            pushes.append(
                {"revs": revs, "failures": failures, "passes": passes,}
            )

        return pushes, math.floor(0.9 * len(pushes))

    # To properly test the performance of our model, we need to split the data
    # according to time: we train on older pushes and evaluate on newer pushes.
    def train_test_split(self, X, y):
        pushes, train_push_len = self.get_pushes()
        train_len = sum(
            len(push["failures"]) + len(push["passes"])
            for push in pushes[:train_push_len]
        )
        print(
            f"{train_push_len} pushes in the training set (corresponding to {train_len} push/jobs)"
        )
        return X[:train_len], X[train_len:], y[:train_len], y[train_len:]

    def items_gen(self, classes):
        commit_map = get_commit_map()

        for revs, test_datas in test_scheduling.get_test_scheduling_history(
            self.granularity
        ):
            commits = tuple(
                commit_map[revision] for revision in revs if revision in commit_map
            )
            if len(commits) == 0:
                continue

            for test_data in test_datas:
                name = test_data["name"]

                if (revs[0], name) not in classes:
                    continue

                commit_data = commit_features.merge_commits(commits)
                commit_data["test_job"] = test_data
                yield commit_data, classes[(revs[0], name)]

    def get_labels(self):
        classes = {}
        pushes, _ = self.get_pushes()

        for push in pushes:
            for name in push["failures"]:
                classes[(push["revs"][0], name)] = 1

            for name in push["passes"]:
                classes[(push["revs"][0], name)] = 0

        print("{} pushes considered".format(len(pushes)))
        print(
            "{} pushes with at least one failure".format(
                sum(1 for push in pushes if len(push["failures"]) > 0)
            )
        )
        print(
            "{} push/jobs failed".format(
                sum(1 for label in classes.values() if label == 1)
            )
        )
        print(
            "{} push/jobs did not fail".format(
                sum(1 for label in classes.values() if label == 0)
            )
        )

        return classes, [0, 1]

    def select_tests(self, commits, confidence=0.3, push_num=None):
        commit_data = commit_features.merge_commits(commits)

        past_failures_data = test_scheduling.get_past_failures(self.granularity)

        if push_num is None:
            push_num = past_failures_data["push_num"] + 1
        all_runnables = past_failures_data["all_runnables"]

        commit_tests = []
        for data in test_scheduling.generate_data(
            past_failures_data, commit_data, push_num, all_runnables, [], []
        ):
            if self.granularity == "label" and not data["name"].startswith("test-"):
                continue

            commit_test = commit_data.copy()
            commit_test["test_job"] = data
            commit_tests.append(commit_test)

        probs = self.classify(commit_tests, probabilities=True)
        selected_indexes = np.argwhere(probs[:, 1] >= confidence)[:, 0]
        return {
            commit_tests[i]["test_job"]["name"]: math.floor(probs[i, 1] * 100) / 100
            for i in selected_indexes
        }

    def reduce(self, tasks: Set[str], min_redundancy_confidence: float) -> Set[str]:
        failing_together = test_scheduling.get_failing_together_db()

        priorities = ["linux", "win", "mac", "android"]

        to_drop = set()
        to_analyze = sorted(tasks)
        while len(to_analyze) > 1:
            task1 = to_analyze.pop(0)

            for task2 in to_analyze:
                key = f"{task1}${task2}".encode("utf-8")
                if key not in failing_together:
                    continue

                support, confidence = struct.unpack("ff", failing_together[key])
                if confidence < min_redundancy_confidence:
                    continue

                for priority in priorities:
                    if priority in task1:
                        to_drop.add(task2)
                        break
                    elif priority in task2:
                        to_drop.add(task1)
                        break

            to_analyze = [t for t in to_analyze if t not in to_drop]

        return tasks - to_drop

    def evaluation(self):
        # Get a test set of pushes on which to test the model.
        pushes, train_push_len = self.get_pushes()

        test_pushes = pushes[train_push_len:]

        all_tasks = reduce(
            lambda x, y: x | y,
            (set(push["failures"]) | set(push["passes"]) for push in test_pushes[-28:]),
        )

        test_pushes = {push["revs"][0]: push for push in test_pushes}

        print(
            f"Testing on {len(pushes) - train_push_len} out of {len(pushes)}. {len(all_tasks)} schedulable tasks."
        )

        commit_map = get_commit_map()

        past_failures_data = test_scheduling.get_past_failures(self.granularity)
        last_push_num = past_failures_data["push_num"]
        past_failures_data.close()

        # Select tests for all the pushes in the test set.
        for i, (rev, push) in enumerate(tqdm(test_pushes.items())):
            commits = tuple(
                commit_map[revision]
                for revision in push["revs"]
                if revision in commit_map
            )
            if len(commits) == 0:
                continue

            push_num = last_push_num - (len(test_pushes) - (i + 1))

            # Note: we subtract 100 to the push number to make sure we don't use
            # past failure data for the push itself.
            # The number 100 comes from the fact that in the past failure data
            # generation we store past failures in batches of 100 pushes.
            test_pushes[rev]["all_possibly_selected"] = self.select_tests(
                commits, 0.3, push_num - 100
            )

        reductions = [None]
        if self.granularity == "label":
            reductions += [0.7, 0.8, 0.9, 1.0]

        for reduction in reductions:
            for confidence_threshold in [0.3, 0.5, 0.7, 0.8]:
                for rev, push in test_pushes.items():
                    selected = set(
                        name
                        for name, confidence in push["all_possibly_selected"].items()
                        if confidence >= confidence_threshold
                    )

                    if reduction is not None:
                        selected = self.reduce(selected, reduction)

                    caught = selected & set(push["failures"])

                    push["number_scheduled"] = len(selected)
                    push["caught_one"] = (
                        len(caught) > 0 if len(push["failures"]) != 0 else None
                    )
                    push["some_didnt_run"] = (
                        not selected.issubset(
                            set(push["passes"]) | set(push["failures"])
                        ),
                    )
                    push["caught_percentage"] = (
                        len(caught) / len(push["failures"])
                        if len(push["failures"]) != 0
                        else None
                    )

                min_scheduled = min(
                    result["number_scheduled"] for result in test_pushes.values()
                )
                max_scheduled = max(
                    result["number_scheduled"] for result in test_pushes.values()
                )
                average_scheduled = statistics.mean(
                    result["number_scheduled"] for result in test_pushes.values()
                )
                num_failing_pushes = sum(
                    1
                    for result in test_pushes.values()
                    if result["caught_one"] is not None
                )
                num_caught_one = sum(
                    1 for result in test_pushes.values() if result["caught_one"]
                )
                num_caught_one_or_some_didnt_run = sum(
                    1
                    for result in test_pushes.values()
                    if result["caught_one"]
                    or (result["caught_one"] is not None and result["some_didnt_run"])
                )
                percentage_caught_one = 100 * num_caught_one / num_failing_pushes
                percentage_caught_one_or_some_didnt_run = (
                    100 * num_caught_one_or_some_didnt_run / num_failing_pushes
                )
                average_caught_percentage = 100 * statistics.mean(
                    result["caught_percentage"]
                    for result in test_pushes.values()
                    if result["caught_percentage"] is not None
                )

                reduction_str = (
                    f"enabled at {reduction * 100}%"
                    if reduction is not None
                    else "disabled"
                )

                print(
                    f"For confidence threshold {confidence_threshold}, with reduction {reduction_str}: scheduled {average_scheduled} tasks on average (min {min_scheduled}, max {max_scheduled}). In {percentage_caught_one}% of pushes we caught at least one failure ({percentage_caught_one_or_some_didnt_run}% ignoring misses when some of our selected tasks didn't run). On average, we caught {average_caught_percentage}% of all seen failures."
                )

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()


class TestLabelSelectModel(TestSelectModel):
    def __init__(self, lemmatization=False):
        TestSelectModel.__init__(self, lemmatization, "label")


class TestGroupSelectModel(TestSelectModel):
    def __init__(self, lemmatization=False):
        TestSelectModel.__init__(self, lemmatization, "group")
