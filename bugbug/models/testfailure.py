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

from bugbug import commit_features, repository, test_scheduling, utils
from bugbug.model import CommitModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestFailureModel(CommitModel):
    def __init__(self, lemmatization=False):
        CommitModel.__init__(self, lemmatization)

        self.training_dbs.append(test_scheduling.TEST_LABEL_SCHEDULING_DB)

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
            # commit_features.author_experience(),
            # commit_features.reviewer_experience(),
            commit_features.ReviewersNum(),
            # commit_features.component_touched_prev(),
            # commit_features.directory_touched_prev(),
            # commit_features.file_touched_prev(),
            commit_features.Types(),
            commit_features.Files(),
            commit_features.Components(),
            commit_features.ComponentsModifiedNum(),
            commit_features.Directories(),
            commit_features.DirectoriesModifiedNum(),
            commit_features.SourceCodeFilesModifiedNum(),
            commit_features.OtherFilesModifiedNum(),
            commit_features.TestFilesModifiedNum(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "commit_extractor",
                    commit_features.CommitExtractor(feature_extractors, []),
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

    def items_gen(self, classes):
        commit_map = {}

        for commit in repository.get_commits():
            commit_map[commit["node"]] = commit

        assert len(commit_map) > 0

        for revs, test_datas in test_scheduling.get_test_scheduling_history("label"):
            if revs[0] not in classes:
                continue

            commits = tuple(
                commit_map[revision] for revision in revs if revision in commit_map
            )
            if len(commits) == 0:
                continue

            commit_data = commit_features.merge_commits(commits)
            yield commit_data, classes[revs[0]]

    def get_labels(self):
        classes = {}

        for revs, test_datas in test_scheduling.get_test_scheduling_history("label"):
            rev = revs[0]

            if any(
                test_data["is_likely_regression"] or test_data["is_possible_regression"]
                for test_data in test_datas
            ):
                classes[rev] = 1
            else:
                classes[rev] = 0

        logger.info("%d commits failed", sum(label == 1 for label in classes.values()))
        logger.info(
            "%d commits did not fail",
            sum(label == 0 for label in classes.values()),
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
