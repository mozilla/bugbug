# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools
import logging
from datetime import datetime

import dateutil.parser
import numpy as np
import xgboost
from dateutil.relativedelta import relativedelta
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bugzilla, commit_features, db, feature_cleanup, repository, utils
from bugbug.model import CommitModel
from bugbug.model_calibration import IsotonicRegressionCalibrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


# Risk bands, as (name, share of bugs in the band). The model separates patches much better
# at the high end of the risk scale than at the low end, so the bands get narrower as the
# risk increases.
RISK_BAND_SHARES = (
    ("l", 0.5),  # Lower than average risk.
    ("a", 0.3),  # Average risk.
    ("h", 0.15),  # Higher than average risk.
    ("v", 0.05),  # Very high risk.
)


def compute_risk_bands(risks: np.ndarray) -> list[tuple[str, float, float]]:
    """Split the risk scale into contiguous bands holding the RISK_BAND_SHARES of risks.

    Calibrated risks take few distinct values, so a plain quantile could fall in a large
    group of tied risks and leave a band almost empty. Instead, each threshold is the
    distinct risk value whose share of risks at or below it is closest to the target.
    A risk equal to a threshold belongs to the lower band.
    """
    values, counts = np.unique(risks, return_counts=True)
    # Share of risks lower than or equal to each distinct value.
    cumulative = np.cumsum(counts) / counts.sum()
    targets = np.cumsum([share for _, share in RISK_BAND_SHARES[:-1]])

    thresholds = [
        0.0,
        *[float(values[np.argmin(np.abs(cumulative - target))]) for target in targets],
        1.0,
    ]

    return [
        (name, thresholds[i], thresholds[i + 1])
        for i, (name, _) in enumerate(RISK_BAND_SHARES)
    ]


def log_risk_bands(
    risk_bands: list[tuple[str, float, float]],
    risks: np.ndarray,
    is_regressor: np.ndarray,
) -> None:
    logger.info("Average regression rate is %0.3f", is_regressor.mean())

    lower_bound = -1.0
    for name, start, end in risk_bands:
        mask = (risks > lower_bound) & (risks <= end)
        lower_bound = end
        logger.info(
            "Risk band %s (%0.4f-%0.4f): %d bugs, regression rate %0.3f",
            name,
            start,
            end,
            mask.sum(),
            is_regressor[mask].mean() if mask.any() else 0.0,
        )


class RegressorModel(CommitModel):
    risk_bands: list[tuple[str, float, float]] | None = None

    def __init__(
        self,
        calibration: bool = True,
        lemmatization: bool = False,
        interpretable: bool = False,
        use_finder: bool = False,
        exclude_finder: bool = True,
        finder_regressions_only: bool = False,
    ) -> None:
        CommitModel.__init__(self, lemmatization)

        self.training_dbs += [BUG_INTRODUCING_COMMITS_DB, bugzilla.BUGS_DB]
        if finder_regressions_only:
            self.training_dbs.append(BUG_FIXING_COMMITS_DB)

        self.store_dataset = True

        self.use_finder = use_finder
        self.exclude_finder = exclude_finder
        assert use_finder ^ exclude_finder, (
            "Using both use_finder and exclude_finder option does not make a lot of sense"
        )
        self.finder_regressions_only = finder_regressions_only

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
            commit_features.AuthorExperience(),
            commit_features.ReviewerExperience(),
            commit_features.ReviewersNum(),
            commit_features.ComponentTouchedPrev(),
            commit_features.DirectoryTouchedPrev(),
            commit_features.FileTouchedPrev(),
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

        column_transformers = [
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
            ]
        )
        estimator = xgboost.XGBClassifier(
            n_estimators=600,
            learning_rate=0.03,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.5,
            min_child_weight=5,
            n_jobs=utils.get_physical_cpu_count(),
        )
        if calibration:
            estimator = IsotonicRegressionCalibrator(estimator)
            # This is a temporary workaround for the error : "Model type not yet supported by TreeExplainer"
            self.calculate_importance = False
        self.clf = Pipeline(
            [
                ("union", ColumnTransformer(column_transformers)),
                ("estimator", estimator),
            ]
        )
        self.train_bug_ids: list[int] = []

    def items_gen(self, classes):
        # Remember the bug of each item, to weight training samples by bug (see get_sample_weights).
        self.train_bug_ids = []
        for commit, label in super().items_gen(classes):
            self.train_bug_ids.append(commit["bug_id"])
            yield commit, label

    def get_sample_weights(self, X, y):
        # Labels are per bug (a commit is a regressor if its bug caused a regression), and
        # predictions are evaluated per bug, so bugs with many commits would otherwise dominate
        # training: each bug gets the same total weight, split among its commits. The classes
        # are balanced with weights too (instead of undersampling, which would not keep the
        # weights aligned with the samples).
        bug_ids = np.array(self.train_bug_ids)[np.asarray(X.index)]
        _, inverse, counts = np.unique(bug_ids, return_inverse=True, return_counts=True)
        sample_weight = 1.0 / counts[inverse]
        for label in np.unique(y):
            mask = y == label
            sample_weight[mask] *= len(y) / (2 * sample_weight[mask].sum())
        return sample_weight

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

            # Only use the last two years of data, for both classes. The clean set can't go
            # further back anyway, as "Regressed by" data is only reliable since then (see
            # https://groups.google.com/g/mozilla.dev.platform/c/SjjW6_O-FqM/m/G-CrIVT2BAAJ).
            # Taking regressors from a longer period than clean commits would let the model
            # learn "old-looking patch" (e.g. lower experience counts) instead of "risky patch".
            if push_date < datetime.utcnow() - relativedelta(years=2):
                continue

            node = commit_data["node"]
            if commit_data["bug_id"] in regressor_bugs or (
                self.use_finder and node in regressors
            ):
                classes[node] = 1
            elif not self.exclude_finder or node not in regressors:
                # We remove the last 3 months, as there could be regressions which haven't been
                # filed yet. While it is true that some regressions might not be found for a long
                # time, more than 3 months seems overly conservative.
                # There will be some patches we currently add to the clean set and will later move
                # to the regressor set, but they are a very small subset.
                if push_date > datetime.utcnow() - relativedelta(months=3):
                    continue

                classes[node] = 0

        logger.info(
            "%d commits caused regressions",
            sum(label == 1 for label in classes.values()),
        )

        logger.info(
            "%d commits did not cause regressions",
            sum(label == 0 for label in classes.values()),
        )

        return classes, [0, 1]

    def find_risk_band(self, risk: float) -> str:
        risk_bands = self.risk_bands
        assert risk_bands is not None, "The risk bands are computed at training time"

        for name, start, end in risk_bands:
            if start <= risk <= end:
                return name

        raise AssertionError()

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

            # Use the past months of data (make sure it is not also used for training!).
            if push_date < datetime.utcnow() - relativedelta(months=EVALUATION_MONTHS):
                continue

            commits.append(commit_data)

        logger.info("%d commits in the evaluation set", len(commits))
        bugs_num = len(set(commit["bug_id"] for commit in commits))
        logger.info("%d bugs in the evaluation set", bugs_num)

        # Sort commits by bug ID, so we can use itertools.groupby to group them by bug ID.
        commits.sort(key=lambda x: x["bug_id"])

        risks = []
        is_regressor = []
        for bug_id, commit_iter in itertools.groupby(commits, lambda x: x["bug_id"]):
            probs = self.classify(list(commit_iter), probabilities=True)
            risks.append(max(probs[:, 1]))
            is_regressor.append(bug_id in bug_regressors)

        if len(risks) == 0:
            logger.warning("No bugs in the evaluation set, risk bands not computed")
            return

        self.risk_bands = compute_risk_bands(np.array(risks))

        # Regressions for recent patches might not have been filed yet, so the rates are
        # lower than the actual ones, but their ratios between bands are still meaningful.
        log_risk_bands(self.risk_bands, np.array(risks), np.array(is_regressor))

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, commits, classes, probabilities):
        for i, commit in enumerate(commits):
            if repository.is_wptsync(commit):
                classes[i] = 0 if not probabilities else [1.0, 0.0]

        return classes
