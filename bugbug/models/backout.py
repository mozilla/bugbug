# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import commit_features, feature_cleanup, repository
from bugbug.model import CommitModel


class BackoutModel(CommitModel):
    def __init__(self, lemmatization=False):
        CommitModel.__init__(self, lemmatization)

        self.calculate_importance = False

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
            commit_features.files_modified_num(),
            commit_features.file_size(),
            commit_features.test_added(),
            commit_features.added(),
            commit_features.deleted(),
            commit_features.test_deleted(),
            commit_features.author_experience(),
            commit_features.reviewer_experience(),
            commit_features.component_touched_prev(),
            commit_features.directory_touched_prev(),
            commit_features.file_touched_prev(),
            commit_features.types(),
            commit_features.components(),
            commit_features.directories(),
            commit_features.files(),
        ]

        cleanup_functions = [
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "commit_extractor",
                    commit_features.CommitExtractor(
                        feature_extractors, cleanup_functions
                    ),
                ),
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("desc", self.text_vectorizer(), "desc"),
                        ]
                    ),
                ),
            ]
        )

        self.clf = xgboost.XGBClassifier(n_jobs=16)
        self.clf.set_params(predictor="cpu_predictor")

    def get_labels(self):
        classes = {}

        for commit_data in repository.get_commits():
            classes[commit_data["node"]] = 1 if commit_data["ever_backedout"] else 0

        print(
            "{} commits were backed out".format(
                sum(1 for label in classes.values() if label == 1)
            )
        )
        print(
            "{} commits were not backed out".format(
                sum(1 for label in classes.values() if label == 0)
            )
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()
