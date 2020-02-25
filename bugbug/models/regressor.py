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

from bugbug import commit_features, db, feature_cleanup, repository
from bugbug.model import CommitModel

BUG_FIXING_COMMITS_DB = "data/bug_fixing_commits.json"
db.register(
    BUG_FIXING_COMMITS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/bug_fixing_commits.json.zst",
    1,
)

BUG_INTRODUCING_COMMITS_DB = "data/bug_introducing_commits.json"
db.register(
    BUG_INTRODUCING_COMMITS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/bug_introducing_commits.json.zst",
    2,
)

TOKENIZED_BUG_INTRODUCING_COMMITS_DB = "data/tokenized_bug_introducing_commits.json"
db.register(
    TOKENIZED_BUG_INTRODUCING_COMMITS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/tokenized_bug_introducing_commits.json.zst",
    3,
)


class RegressorModel(CommitModel):
    def __init__(self, lemmatization=False, interpretable=False):
        CommitModel.__init__(self, lemmatization)

        self.required_dbs.append(BUG_INTRODUCING_COMMITS_DB)

        self.store_dataset = True
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
            commit_features.author_experience(),
            commit_features.reviewer_experience(),
            commit_features.reviewers_num(),
            commit_features.component_touched_prev(),
            commit_features.directory_touched_prev(),
            commit_features.file_touched_prev(),
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

        column_transformers = [("data", DictVectorizer(), "data")]

        if not interpretable:
            column_transformers.append(
                ("desc", self.text_vectorizer(min_df=0.0001), "desc")
            )

        self.extraction_pipeline = Pipeline(
            [
                (
                    "commit_extractor",
                    commit_features.CommitExtractor(
                        feature_extractors, cleanup_functions
                    ),
                ),
                ("union", ColumnTransformer(column_transformers)),
            ]
        )

        self.clf = xgboost.XGBClassifier(n_jobs=16)
        self.clf.set_params(predictor="cpu_predictor")

    def get_labels(self):
        classes = {}

        regressors = set(
            r["bug_introducing_rev"]
            for r in db.read(BUG_INTRODUCING_COMMITS_DB)
            if r["bug_introducing_rev"]
        )

        for commit_data in repository.get_commits():
            if commit_data["ever_backedout"]:
                continue

            node = commit_data["node"]
            if node in regressors:
                classes[node] = 1
            else:
                push_date = dateutil.parser.parse(commit_data["pushdate"])

                # The labels we have are only from two years and six months ago (see the regressor finder script).
                if push_date < datetime.utcnow() - relativedelta(years=2, months=6):
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
