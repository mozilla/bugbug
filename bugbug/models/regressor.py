# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import datetime

import dateutil.parser
import xgboost
from dateutil.relativedelta import relativedelta
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import commit_features, feature_cleanup, labels, repository
from bugbug.model import CommitModel


class RegressorModel(CommitModel):
    def __init__(self, lemmatization=False):
        CommitModel.__init__(self, lemmatization)

        self.calculate_importance = False

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
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
            commit_features.components_modified_num(),
            commit_features.directories(),
            commit_features.directories_modified_num(),
            commit_features.files(),
            commit_features.files_modified_num(),
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

        regressors = set(r[0] for r in labels.get_labels("regressor"))

        for commit_data in repository.get_commits():
            if commit_data["ever_backedout"]:
                continue

            node = commit_data["node"]
            if node in regressors:
                classes[node] = 1
            else:
                push_date = dateutil.parser.parse(commit_data["pushdate"])

                # The labels we have are only from 2016-11-01.
                # TODO: Automate collection of labels and somehow remove this check.
                if push_date < datetime(2016, 11, 1):
                    continue

                # We remove the last 6 months, as there could be regressions which haven't been filed yet.
                if push_date > datetime.utcnow() - relativedelta(months=6):
                    continue

                classes[node] = 0

        print(
            "{} commits caused regressions".format(
                sum(1 for label in classes.values() if label == 1)
            )
        )

        print(
            "{} commits did not cause regressions".format(
                sum(1 for label in classes.values() if label == 0)
            )
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()
