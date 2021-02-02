# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bugzilla, commit_features, feature_cleanup, labels, repository, utils
from bugbug.model import CommitModel


class AnnotateIgnoreModel(CommitModel):
    def __init__(self, lemmatization: bool = False) -> None:
        CommitModel.__init__(self, lemmatization)

        self.calculate_importance = False

        self.training_dbs += [bugzilla.BUGS_DB]

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
            commit_features.source_code_file_size(),
            commit_features.other_file_size(),
            commit_features.test_file_size(),
            commit_features.source_code_added(),
            commit_features.other_added(),
            commit_features.test_added(),
            commit_features.source_code_deleted(),
            commit_features.other_deleted(),
            commit_features.test_deleted(),
            commit_features.reviewers_num(),
            commit_features.types(),
            commit_features.files(),
            commit_features.components(),
            commit_features.components_modified_num(),
            commit_features.directories(),
            commit_features.directories_modified_num(),
            commit_features.source_code_files_modified_num(),
            commit_features.other_files_modified_num(),
            commit_features.test_files_modified_num(),
            commit_features.functions_touched_num(),
            commit_features.functions_touched_size(),
            commit_features.source_code_file_metrics(),
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
                            ("desc", self.text_vectorizer(min_df=0.0001), "desc"),
                        ]
                    ),
                ),
            ]
        )

        self.clf = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
        self.clf.set_params(predictor="cpu_predictor")

    def get_labels(self):
        classes = {}

        # Commits in regressor or regression bugs usually are not formatting changes.
        regression_related_bugs = set(
            sum(
                (
                    bug["regressed_by"] + bug["regressions"]
                    for bug in bugzilla.get_bugs()
                ),
                [],
            )
        )

        for commit_data in repository.get_commits(include_ignored=True):
            if commit_data["backedoutby"]:
                continue

            node = commit_data["node"]

            if commit_data["ignored"]:
                classes[node] = 1
            elif commit_data["bug_id"] in regression_related_bugs:
                classes[node] = 0

        for node, label in labels.get_labels("annotateignore"):
            classes[node] = int(label)

        print(
            "{} commits that can be ignored".format(
                sum(1 for label in classes.values() if label == 1)
            )
        )

        print(
            "{} commits that cannot be ignored".format(
                sum(1 for label in classes.values() if label == 0)
            )
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()
