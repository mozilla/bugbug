# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools
from datetime import datetime

import dateutil.parser
import numpy as np
import xgboost
from dateutil.relativedelta import relativedelta
from imblearn.under_sampling import RandomUnderSampler
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bugzilla, commit_features, db, feature_cleanup, repository, utils
from bugbug.model import CommitModel

BUG_FIXING_COMMITS_DB = "data/bug_fixing_commits.json"
db.register(
    BUG_FIXING_COMMITS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/bug_fixing_commits.json.zst",
    2,
)

BUG_INTRODUCING_COMMITS_DB = "data/bug_introducing_commits.json"
db.register(
    BUG_INTRODUCING_COMMITS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/bug_introducing_commits.json.zst",
    3,
)

TOKENIZED_BUG_INTRODUCING_COMMITS_DB = "data/tokenized_bug_introducing_commits.json"
db.register(
    TOKENIZED_BUG_INTRODUCING_COMMITS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/tokenized_bug_introducing_commits.json.zst",
    3,
)

EVALUATION_MONTHS = 3


class RegressorModel(CommitModel):
    def __init__(
        self,
        lemmatization: bool = False,
        interpretable: bool = True,
        use_finder: bool = False,
        exclude_finder: bool = True,
        finder_regressions_only: bool = False,
    ) -> None:
        CommitModel.__init__(self, lemmatization)

        self.training_dbs += [BUG_INTRODUCING_COMMITS_DB, bugzilla.BUGS_DB]
        if finder_regressions_only:
            self.training_dbs.append(BUG_FIXING_COMMITS_DB)

        self.store_dataset = True
        self.sampler = RandomUnderSampler(random_state=0)

        self.use_finder = use_finder
        self.exclude_finder = exclude_finder
        assert (
            use_finder ^ exclude_finder
        ), "Using both use_finder and exclude_finder option does not make a lot of sense"
        self.finder_regressions_only = finder_regressions_only

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

        self.clf = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
        self.clf.set_params(predictor="cpu_predictor")

    def get_labels(self):
        classes = {}

        if self.use_finder or self.exclude_finder:
            if self.finder_regressions_only:
                regression_fixes = set(
                    bug_fixing_commit["rev"]
                    for bug_fixing_commit in db.read(BUG_FIXING_COMMITS_DB)
                    if bug_fixing_commit["type"] == "r"
                )

            regressors = set(
                r["bug_introducing_rev"]
                for r in db.read(BUG_INTRODUCING_COMMITS_DB)
                if r["bug_introducing_rev"]
                and (
                    not self.finder_regressions_only
                    or r["bug_fixing_rev"] in regression_fixes
                )
            )

        regressor_bugs = set(
            sum((bug["regressed_by"] for bug in bugzilla.get_bugs()), [])
        )

        for commit_data in repository.get_commits():
            if commit_data["backedoutby"]:
                continue

            if repository.is_wptsync(commit_data):
                continue

            push_date = dateutil.parser.parse(commit_data["pushdate"])

            # Skip commits used for the evaluation phase.
            if push_date > datetime.utcnow() - relativedelta(months=EVALUATION_MONTHS):
                continue

            node = commit_data["node"]
            if commit_data["bug_id"] in regressor_bugs or (
                self.use_finder and node in regressors
            ):
                classes[node] = 1
            elif not self.exclude_finder or node not in regressors:
                # The labels we have are only from two years ago (see https://groups.google.com/g/mozilla.dev.platform/c/SjjW6_O-FqM/m/G-CrIVT2BAAJ).
                # While we can go further back with the regressor finder script, it isn't remotely
                # as precise as the "Regressed By" data.
                # In the future, we might want to re-evaluate this limit (e.g. extend ), but we
                # have to be careful (using too old patches might cause worse results as patch
                # characteristics evolve over time).
                if push_date < datetime.utcnow() - relativedelta(years=2):
                    continue

                # We remove the last 3 months, as there could be regressions which haven't been
                # filed yet. While it is true that some regressions might not be found for a long
                # time, more than 3 months seems overly conservative.
                # There will be some patches we currently add to the clean set and will later move
                # to the regressor set, but they are a very small subset.
                if push_date > datetime.utcnow() - relativedelta(months=3):
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

    def evaluation(self) -> None:
        bug_regressors = set(
            sum((bug["regressed_by"] for bug in bugzilla.get_bugs()), [])
        )

        commits = []

        for commit_data in repository.get_commits():
            if commit_data["backedoutby"]:
                continue

            if repository.is_wptsync(commit_data):
                continue

            push_date = dateutil.parser.parse(commit_data["pushdate"])

            # Use the past two months of data (make sure it is not also used for training!).
            if push_date < datetime.utcnow() - relativedelta(months=EVALUATION_MONTHS):
                continue

            commits.append(commit_data)

        print(f"{len(commits)} commits in the evaluation set")
        bugs_num = len(set(commit["bug_id"] for commit in commits))
        print(f"{bugs_num} bugs in the evaluation set")

        # Sort commits by bug ID, so we can use itertools.groupby to group them by bug ID.
        commits.sort(key=lambda x: x["bug_id"])

        results = []
        for bug_id, commit_iter in itertools.groupby(commits, lambda x: x["bug_id"]):
            probs = self.classify(list(commit_iter), probabilities=True)
            results.append((max(probs[:, 1]), bug_id in bug_regressors))

        # Let's define the risk bands relatively to average risk.
        # On average, around 1 out of 8 (13%) patches cause regressions.
        # Risk band 1 - around 1 out of 15 (7%) patches within this risk band cause regressions.
        # Risk band 2 - around 1 out of 7 (15%) patches within this risk band cause regressions.
        # Risk bank 3 - around 1 out of 3 (35%) patches within this risk band cause regressions.

        # Step 1. Calculate % of patches which cause regressions.
        total_landings = len(results)
        total_regressions = sum(1 for _, is_reg in results if is_reg)
        average_regression_rate = total_regressions / total_landings

        print(f"Average risk is {average_regression_rate}")

        MIN_SAMPLE = 200

        # Step 2. Define risk band 1 (half than average risk).
        max_band1_prob = 1.0
        total_landings = 0
        total_regressions = 0
        results.sort(key=lambda x: x[0])
        for prob, is_reg in results:
            total_landings += 1
            if is_reg:
                total_regressions += 1

            if total_landings < MIN_SAMPLE:
                continue

            print(
                f"{total_regressions} out of {total_landings} patches with risk lower than {prob} caused regressions ({total_regressions / total_landings}"
            )

            # No need to go further, since we are interested in half than average risk.
            if (
                total_regressions / total_landings
                >= (average_regression_rate / 2) + 0.01
            ):
                max_band1_prob = prob
                break

        print("\n\n")

        # Step 3. Define risk band 3 (double than average risk).
        min_band3_prob = 0.0
        total_landings = 0
        total_regressions = 0
        results.sort(key=lambda x: x[0], reverse=True)
        for prob, is_reg in results:
            total_landings += 1
            if is_reg:
                total_regressions += 1

            if total_landings < MIN_SAMPLE:
                continue

            print(
                f"{total_regressions} out of {total_landings} patches with risk higher than {prob} caused regressions ({total_regressions / total_landings}"
            )

            # No need to go further, since we are interested in double than average risk.
            if (
                total_regressions / total_landings
                <= (average_regression_rate * 2) - 0.01
            ):
                min_band3_prob = prob
                break

        print("\n\n")

        # Step 4. Define risk band 2 (average risk).
        results.sort(key=lambda x: x[0])
        for prob_start in np.arange(max_band1_prob / 2, max_band1_prob + 0.02, 0.01):
            for prob_end in np.arange(min_band3_prob - 0.02, 0.99, 0.01):
                total_landings = 0
                total_regressions = 0
                for prob, is_reg in results:
                    if prob < prob_start or prob > prob_end:
                        continue

                    total_landings += 1
                    if is_reg:
                        total_regressions += 1

                if total_landings < MIN_SAMPLE:
                    continue

                if (
                    (average_regression_rate / 2) + 0.01
                    > total_regressions / total_landings
                    > (average_regression_rate * 2) - 0.01
                ):
                    continue

                print(
                    f"{total_regressions} out of {total_landings} patches with risk between {prob_start} and {prob_end} caused regressions ({total_regressions / total_landings}"
                )

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()

    def overwrite_classes(self, commits, classes, probabilities):
        for i, commit in enumerate(commits):
            if repository.is_wptsync(commit):
                classes[i] = 0 if not probabilities else [1.0, 0.0]

        return classes
