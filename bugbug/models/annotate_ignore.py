# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

import xgboost
from imblearn.pipeline import Pipeline as ImblearnPipeline
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bugzilla, commit_features, feature_cleanup, labels, repository, utils
from bugbug.model import CommitModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AnnotateIgnoreModel(CommitModel):
    def __init__(self, lemmatization: bool = False) -> None:
        CommitModel.__init__(self, lemmatization)

        self.calculate_importance = False
        self.cross_validation_enabled = False

        self.training_dbs += [bugzilla.BUGS_DB]

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
            ]
        )

        self.clf = ImblearnPipeline(
            [
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("desc", self.text_vectorizer(min_df=0.0001), "desc"),
                            (
                                "files",
                                CountVectorizer(
                                    analyzer=utils.keep_as_is,
                                    lowercase=False,
                                    min_df=0.0014,
                                ),
                                "files",
                            ),
                        ]
                    ),
                ),
                ("sampler", RandomUnderSampler(random_state=0)),
                (
                    "estimator",
                    xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count()),
                ),
            ]
        )

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
            sum(label == 1 for label in classes.values()),
        )

        logger.info(
            "%d commits that cannot be ignored",
            sum(label == 0 for label in classes.values()),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
