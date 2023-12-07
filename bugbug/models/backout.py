# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from datetime import datetime

import dateutil.parser
import xgboost
from dateutil.relativedelta import relativedelta
from imblearn.pipeline import Pipeline as ImblearnPipeline
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, commit_features, feature_cleanup, repository, utils
from bugbug.model import CommitModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BackoutModel(CommitModel):
    def __init__(self, lemmatization=False, bug_data=False):
        CommitModel.__init__(self, lemmatization, bug_data)

        self.calculate_importance = False

        feature_extractors = [
            commit_features.SourceCodeFilesModifiedNum(),
            commit_features.OtherFilesModifiedNum(),
            commit_features.TestFilesModifiedNum(),
            commit_features.SourceCodeFileSize(),
            commit_features.OtherFileSize(),
            commit_features.TestFileSize(),
            commit_features.SourceCodeAdded(),
            commit_features.OtherAdded(),
            commit_features.TestAdded(),
            commit_features.SourceCodeDeleted(),
            commit_features.OtherDeleted(),
            commit_features.TestDeleted(),
            commit_features.AuthorExperience(),
            commit_features.ReviewerExperience(),
            commit_features.ReviewersNum(),
            commit_features.ComponentTouchedPrev(),
            commit_features.DirectoryTouchedPrev(),
            commit_features.FileTouchedPrev(),
            commit_features.Types(),
            commit_features.Components(),
            commit_features.Directories(),
            commit_features.Files(),
        ]

        if bug_data:
            feature_extractors += [
                bug_features.Product(),
                bug_features.Component(),
                bug_features.Severity(),
                bug_features.Priority(),
                bug_features.HasCrashSignature(),
                bug_features.HasRegressionRange(),
                bug_features.Whiteboard(),
                bug_features.Keywords(),
                bug_features.NumberOfBugDependencies(),
                bug_features.BlockedBugsNumber(),
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
                            ("desc", self.text_vectorizer(), "desc"),
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

        two_years_and_six_months_ago = datetime.utcnow() - relativedelta(
            years=2, months=6
        )

        for commit_data in repository.get_commits():
            pushdate = dateutil.parser.parse(commit_data["pushdate"])
            if pushdate < two_years_and_six_months_ago:
                continue

            classes[commit_data["node"]] = 1 if commit_data["backedoutby"] else 0

        logger.info(
            "%d commits were backed out",
            sum(label == 1 for label in classes.values()),
        )
        logger.info(
            "%d commits were not backed out",
            sum(label == 0 for label in classes.values()),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
