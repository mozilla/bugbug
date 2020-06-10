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

from bugbug import bug_features, commit_features, feature_cleanup, repository, utils
from bugbug.model import CommitModel


class BackoutModel(CommitModel):
    def __init__(self, lemmatization=False, bug_data=False):
        CommitModel.__init__(self, lemmatization, bug_data)

        self.calculate_importance = False

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
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
            commit_features.author_experience(),
            commit_features.reviewer_experience(),
            commit_features.reviewers_num(),
            commit_features.component_touched_prev(),
            commit_features.directory_touched_prev(),
            commit_features.file_touched_prev(),
            commit_features.types(),
            commit_features.components(),
            commit_features.directories(),
            commit_features.files(),
        ]

        if bug_data:
            feature_extractors += [
                bug_features.product(),
                bug_features.component(),
                bug_features.severity(),
                bug_features.priority(),
                bug_features.has_crash_signature(),
                bug_features.has_regression_range(),
                bug_features.whiteboard(),
                bug_features.keywords(),
                bug_features.number_of_bug_dependencies(),
                bug_features.blocked_bugs_number(),
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

        self.clf = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
        self.clf.set_params(predictor="cpu_predictor")

    def get_labels(self):
        classes = {}

        two_years_and_six_months_ago = datetime.utcnow() - relativedelta(
            years=2, months=6
        )

        for commit_data in repository.get_commits():
            pushdate = dateutil.parser.parse(commit_data["pushdate"])
            if pushdate < two_years_and_six_months_ago:
                continue

            classes[commit_data["node"]] = 1 if commit_data["backedoutby"] else 0

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
