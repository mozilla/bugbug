# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import math

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
    def __init__(self, lemmatization=False):
        Model.__init__(self, lemmatization)

        self.required_dbs = [repository.COMMITS_DB, test_scheduling.TEST_SCHEDULING_DB]

        self.calculate_importance = False
        self.cross_validation_enabled = False

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
            commit_features.files_modified_num(),
            commit_features.test_files_modified_num(),
            commit_features.file_size(),
            commit_features.test_file_size(),
            commit_features.added(),
            commit_features.test_added(),
            commit_features.deleted(),
            commit_features.test_deleted(),
            test_scheduling_features.name(),
            test_scheduling_features.platform(),
            test_scheduling_features.chunk(),
            test_scheduling_features.suite(),
            test_scheduling_features.prev_failures(),
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
        train_len = math.floor(0.9 * X.shape[0])
        return X[:train_len], X[train_len:], y[:train_len], y[train_len:]

    def items_gen(self, classes):
        commit_map = {}

        for commit in repository.get_commits():
            commit_map[commit["node"]] = commit

        assert len(commit_map) > 0

        # TODO: Data from multiple commits in the same push should be merged.
        for test_data in test_scheduling.get_test_scheduling_history():
            rev = test_data["rev"]
            name = test_data["name"]

            if (rev, name) not in classes:
                continue

            if rev not in commit_map:
                continue

            commit_data = commit_map[rev]
            commit_data["test_job"] = test_data
            yield commit_data, classes[(rev, name)]

    def get_labels(self):
        classes = {}

        for test_data in test_scheduling.get_test_scheduling_history():
            rev = test_data["rev"]
            name = test_data["name"]

            if not name.startswith("test-"):
                continue

            if test_data["is_likely_regression"] or test_data["is_possible_regression"]:
                classes[(rev, name)] = 1
            else:
                classes[(rev, name)] = 0

        print(
            "{} commit/jobs failed".format(
                sum(1 for label in classes.values() if label == 1)
            )
        )
        print(
            "{} commit/jobs did not fail".format(
                sum(1 for label in classes.values() if label == 0)
            )
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()
