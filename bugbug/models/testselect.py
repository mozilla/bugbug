# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import math
import random
from collections import OrderedDict

import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import (
    commit_features,
    repository,
    test_scheduling,
    test_scheduling_features,
)
from bugbug.model import Model


class TestSelectModel(Model):
    def __init__(self, lemmatization=False, granularity="label", use_subset=False):
        Model.__init__(self, lemmatization)

        self.granularity = granularity
        # This is useful for development purposes, it avoids using too much memory
        # by using a subset of the dataset (dropping some passing runnables).
        self.use_subset = use_subset

        self.required_dbs = [repository.COMMITS_DB]
        if granularity == "label":
            self.required_dbs.append(test_scheduling.TEST_LABEL_SCHEDULING_DB)
        elif granularity == "group":
            self.required_dbs.append(test_scheduling.TEST_GROUP_SCHEDULING_DB)

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

    # To properly test the performance of our model, we need to split the data
    # according to time: we train on older pushes and evaluate on newer pushes.
    def train_test_split(self, X, y):
        pushes = OrderedDict()
        for test_data in test_scheduling.get_test_scheduling_history(self.granularity):
            rev = test_data["revs"][0]
            name = test_data["name"]

            if self.granularity == "label" and not name.startswith("test-"):
                continue

            if rev in pushes:
                pushes[rev] += 1
            else:
                pushes[rev] = 1

        train_push_len = math.floor(0.9 * len(pushes))
        train_pushes = list(pushes.values())[:train_push_len]
        train_len = sum(count for count in train_pushes)
        print(
            f"{train_push_len} pushes in the training set (corresponding to {train_len} push/jobs)"
        )
        return X[:train_len], X[train_len:], y[:train_len], y[train_len:]

    def items_gen(self, classes):
        commit_map = {}

        for commit in repository.get_commits():
            commit_map[commit["node"]] = commit

        assert len(commit_map) > 0

        for test_data in test_scheduling.get_test_scheduling_history(self.granularity):
            revs = test_data["revs"]
            name = test_data["name"]

            if (revs[0], name) not in classes:
                continue

            commits = tuple(
                commit_map[revision]
                for revision in test_data["revs"]
                if revision in commit_map
            )
            if len(commits) == 0:
                continue

            commit_data = commit_features.merge_commits(commits)
            commit_data["test_job"] = test_data
            yield commit_data, classes[(revs[0], name)]

    def get_labels(self):
        classes = {}
        pushes = {}

        for test_data in test_scheduling.get_test_scheduling_history(self.granularity):
            rev = test_data["revs"][0]
            name = test_data["name"]

            if self.granularity == "label" and not name.startswith("test-"):
                continue

            if rev not in pushes:
                pushes[rev] = {
                    "failures": [],
                    "passes": [],
                }

            if test_data["is_likely_regression"] or test_data["is_possible_regression"]:
                pushes[rev]["failures"].append(name)
            else:
                pushes[rev]["passes"].append(name)

        if self.use_subset:
            random.seed(0)

            for rev, push in pushes.items():
                push["passes"] = random.sample(
                    push["passes"], math.ceil(len(push["passes"]) / 10)
                )

        for rev, push in pushes.items():
            for name in push["failures"]:
                classes[(rev, name)] = 1

            for name in push["passes"]:
                classes[(rev, name)] = 0

        print("{} pushes considered".format(len(pushes)))
        print(
            "{} pushes with at least one failure".format(
                sum(1 for push in pushes.values() if len(push["failures"]) > 0)
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

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()


class TestLabelSelectModel(TestSelectModel):
    def __init__(self, lemmatization=False):
        TestSelectModel.__init__(self, lemmatization, "label")


class TestGroupSelectModel(TestSelectModel):
    def __init__(self, lemmatization=False):
        TestSelectModel.__init__(self, lemmatization, "group")
