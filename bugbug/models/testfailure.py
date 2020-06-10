# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import xgboost
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import commit_features, repository, test_scheduling, utils
from bugbug.model import CommitModel


class TestFailureModel(CommitModel):
    def __init__(self, lemmatization=False):
        CommitModel.__init__(self, lemmatization)

        self.training_dbs.append(test_scheduling.TEST_LABEL_SCHEDULING_DB)

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
            # commit_features.author_experience(),
            # commit_features.reviewer_experience(),
            commit_features.reviewers_num(),
            # commit_features.component_touched_prev(),
            # commit_features.directory_touched_prev(),
            # commit_features.file_touched_prev(),
            commit_features.types(),
            commit_features.files(),
            commit_features.components(),
            commit_features.components_modified_num(),
            commit_features.directories(),
            commit_features.directories_modified_num(),
            commit_features.source_code_files_modified_num(),
            commit_features.other_files_modified_num(),
            commit_features.test_files_modified_num(),
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

        self.clf = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
        self.clf.set_params(predictor="cpu_predictor")

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

        print(
            "{} commits failed".format(
                sum(1 for label in classes.values() if label == 1)
            )
        )
        print(
            "{} commits did not fail".format(
                sum(1 for label in classes.values() if label == 0)
            )
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()
