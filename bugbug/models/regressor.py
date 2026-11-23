# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import itertools
import logging
from datetime import datetime
from typing import Iterable, Sequence

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
    ("vl", 0.2),  # Very low risk.
    ("l", 0.3),  # Lower than average risk.
    ("a", 0.3),  # Average risk.
    ("h", 0.15),  # Higher than average risk.
    ("v", 0.05),  # Very high risk.
)


def compute_risk_bands(risks: np.ndarray) -> list[tuple[str, float, float]]:
    """Split the risk scale into contiguous bands holding the RISK_BAND_SHARES of risks.

    Calibrated risks can take few distinct values, so a plain quantile could fall in a large
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


# The bug fields used by the features. The product rarely changes, and the creation time
# never does, so their current values are those the bugs had when their patches landed.
BUG_FIELDS = ("product", "creation_time")


def get_bug_fields(bug_ids: set[int]) -> dict[int, dict]:
    """Read the bug fields used by the features from the bugs DB."""
    return {
        bug["id"]: {field: bug[field] for field in BUG_FIELDS}
        for bug in bugzilla.get_bugs()
        if bug["id"] in bug_ids
    }


def fetch_bug_fields(bug_ids: Iterable[int | None]) -> dict[int, dict]:
    """Fetch the bug fields used by the features from Bugzilla.

    Bugs which can't be fetched (e.g. private ones) are left out, so they have no bug features.
    """
    ids = [bug_id for bug_id in bug_ids if bug_id]
    if not ids:
        return {}

    try:
        bugs = bugzilla.get_fields(ids, ("id",) + BUG_FIELDS)
    except Exception:
        logger.warning("Could not fetch bugs %s from Bugzilla", ids, exc_info=True)
        return {}

    return {
        bug_id: {field: bug[field] for field in BUG_FIELDS}
        for bug_id, bug in bugs.items()
        if all(field in bug for field in BUG_FIELDS)
    }


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
            commit_features.SourceCodeMetricsDiff(),
            commit_features.StackSize(),
            commit_features.StackSpread(),
            commit_features.StackTests(),
            commit_features.StackPartPosition(),
            commit_features.BugLandingHistory(),
            commit_features.BugProduct(),
            commit_features.BugAgeAtLanding(),
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
            # Sigmoid calibration keeps the ranking of the classifier (isotonic calibration
            # ties many patches, which costs ~1 point of average precision at bug level).
            estimator = IsotonicRegressionCalibrator(estimator, method="sigmoid")
            # This is a temporary workaround for the error : "Model type not yet supported by TreeExplainer"
            self.calculate_importance = False
        self.clf = Pipeline(
            [
                ("union", ColumnTransformer(column_transformers)),
                ("estimator", estimator),
            ]
        )
        self.train_bug_ids: list[int] = []

    @staticmethod
    def add_stack_and_bug_info(
        commits: Sequence[repository.CommitDict],
        bugs: dict[int, dict],
        stack: Sequence[repository.CommitDict] | None = None,
        stack_index: commit_features.StackInfoIndex | None = None,
    ) -> None:
        """Attach the stack, bug landing history and bug fields used by the features.

        By default, the stack of each commit is made of the commits of the same bug landed in
        the same push, among the commits DB and the given commits (which might not be in the DB
        yet); pass stack for patches under review. The landing histories come from the commits
        DB, or from stack_index if given (e.g. a cached index of all bugs). bugs maps bug IDs to
        the BUG_FIELDS of the bugs (bugs which are missing, e.g. private ones, have no bug
        features).
        """
        if stack_index is None:
            bug_ids = set(commit["bug_id"] for commit in commits if commit["bug_id"])
            stack_index = commit_features.StackInfoIndex(
                repository.get_commits(), bug_ids
            )

        same_push: dict[tuple, list[repository.CommitDict]] = collections.defaultdict(
            list
        )
        for commit in commits:
            same_push[(commit["bug_id"], commit["pushdate"])].append(commit)

        for commit in commits:
            stack_index.attach(
                commit, stack, same_push[(commit["bug_id"], commit["pushdate"])]
            )
            commit["bug"] = bugs.get(commit["bug_id"], {})

    def items_gen(self, classes):
        bug_ids = set(
            commit["bug_id"]
            for commit in repository.get_commits()
            if commit["node"] in classes and commit["bug_id"]
        )
        stack_index = commit_features.StackInfoIndex(repository.get_commits(), bug_ids)
        bugs = get_bug_fields(bug_ids)

        # Remember the bug of each item, to weight training samples by bug (see get_sample_weights).
        self.train_bug_ids = []
        for commit, label in super().items_gen(classes):
            # Same as add_stack_and_bug_info, without collecting the commits first.
            stack_index.attach(commit)
            commit["bug"] = bugs.get(commit["bug_id"], {})
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

        self.add_stack_and_bug_info(
            commits,
            get_bug_fields(set(commit["bug_id"] for commit in commits)),
        )

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
