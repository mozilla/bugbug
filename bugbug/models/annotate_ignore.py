# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bugzilla, commit_features, feature_cleanup, labels, repository, utils
from bugbug.model import CommitModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AnnotateIgnoreModel(CommitModel):
    def __init__(self, lemmatization: bool = False) -> None:
        CommitModel.__init__(self, lemmatization)

        self.calculate_importance = False

        self.training_dbs += [bugzilla.BUGS_DB]

        self.sampler = RandomUnderSampler(random_state=0)

        feature_extractors = [
            commit_features.SourceCodeFileSize(),
            commit_features.OtherFileSize(),
            commit_features.TestFileSize(),
            commit_features.SourceCodeAdded(),
            commit_features.OtherAdded(),
            commit_features.TestAdded(),
            commit_features.SourceCodeDeleted(),
            commit_features.OtherDeleted(),
            commit_features.TestDeleted(),
            commit_features.ReviewersNum(),
            commit_features.Types(),
            commit_features.Files(),
            commit_features.Components(),
            commit_features.ComponentsModifiedNum(),
            commit_features.Directories(),
            commit_features.DirectoriesModifiedNum(),
            commit_features.SourceCodeFilesModifiedNum(),
            commit_features.OtherFilesModifiedNum(),
            commit_features.TestFilesModifiedNum(),
            commit_features.FunctionsTouchedNum(),
            commit_features.FunctionsTouchedSize(),
            commit_features.SourceCodeFileMetrics(),
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

        logger.info(
            "%d commits that can be ignored",
            sum(1 for label in classes.values() if label == 1),
        )

        logger.info(
            "%d commits that cannot be ignored",
            sum(1 for label in classes.values() if label == 0),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names_out()
