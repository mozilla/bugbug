# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import dateutil.parser
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC

from bugbug import bug_features, bugzilla, feature_cleanup, labels
from bugbug.model import BugModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# See DefectModel.get_rollback_point.
TRAINING_SNAPSHOT_AGE = timedelta(days=3)

# Fixed defects which were not identified as regressions within this time are
# considered non-regressions.
NON_REGRESSION_MIN_AGE = timedelta(days=90)


def is_regression_label_change(change: dict) -> bool:
    field = change["field_name"]
    if field == "keywords":
        added = {k.strip() for k in change["added"].split(",")}
        return bool(added & {"regression", "talos-regression"})
    if field == "regressed_by":
        return change["added"] != ""
    if field == "cf_has_regression_range":
        return change["added"] == "yes"
    return False


def get_regression_label_date(bug: dict) -> datetime | None:
    """Return when a bug was first identified as a regression, if ever."""
    for history in bug["history"]:
        if any(is_regression_label_change(change) for change in history["changes"]):
            return dateutil.parser.parse(history["when"])

    return None


class DefectModel(BugModel):
    # Weight of the training samples labelled using the fixed defects rule (see
    # get_bugbug_labels), relative to the other labels.
    fixed_defects_weight = 1.0

    def __init__(self, lemmatization=False, historical=False):
        BugModel.__init__(self, lemmatization)

        # Cross-validation multiplies the (long) training time, the test set
        # evaluation is enough.
        self.cross_validation_enabled = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.Keywords({"regression", "talos-regression", "feature"}),
            bug_features.IsCoverityIssue(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            bug_features.Whiteboard(),
            bug_features.BlockedBugsNumber(),
            bug_features.EverAffected(),
            bug_features.AffectedThenUnaffected(),
            bug_features.Product(),
            bug_features.Component(),
            bug_features.BugReporter(),
            bug_features.OpSys(),
            bug_features.Platform(),
            bug_features.Version(),
            bug_features.FiledVia(),
            bug_features.HasImageAttachmentAtBugCreation(),
            bug_features.HasAttachment(),
        ]

        if historical:
            feature_extractors += [
                bug_features.HadSeverityEnhancement(),
                bug_features.Patches(),
                bug_features.Landings(),
            ]

        cleanup_functions = [
            feature_cleanup.url(),
            feature_cleanup.fileref(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(feature_extractors, cleanup_functions),
                ),
            ]
        )

        # A linear model on sparse n-gram features is as accurate as gradient
        # boosted trees for these tasks, and much faster to train. Probabilities
        # are calibrated on out-of-fold predictions.
        self.clf = Pipeline(
            [
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            (
                                "title",
                                self.text_vectorizer(
                                    min_df=2,
                                    ngram_range=(1, 2),
                                    sublinear_tf=True,
                                    max_features=50000,
                                ),
                                "title",
                            ),
                            (
                                "first_comment",
                                self.text_vectorizer(
                                    min_df=3,
                                    ngram_range=(1, 2),
                                    sublinear_tf=True,
                                    max_features=100000,
                                ),
                                "first_comment",
                            ),
                            (
                                "comments",
                                self.text_vectorizer(
                                    min_df=3,
                                    ngram_range=(1, 2),
                                    sublinear_tf=True,
                                    max_features=100000,
                                ),
                                "comments",
                            ),
                        ]
                    ),
                ),
                (
                    "estimator",
                    CalibratedClassifierCV(
                        LinearSVC(C=0.1), method="isotonic", cv=3, n_jobs=3
                    ),
                ),
            ]
        )

    def get_rollback_point(self, item: dict, label: Any) -> datetime:
        """Return the date bugs are rolled back to, for training.

        The models are applied to recently filed bugs, so they are trained on the
        bugs as they were a few days after being filed. Bugs are rolled back
        further if they were identified as regressions earlier.
        """
        snapshot_date = (
            dateutil.parser.parse(item["creation_time"]) + TRAINING_SNAPSHOT_AGE
        )
        regression_label_date = get_regression_label_date(item)
        if regression_label_date is not None:
            snapshot_date = min(snapshot_date, regression_label_date)
        return snapshot_date

    def items_gen(self, classes):
        # Remember the bug of each item, to weight training samples (see
        # get_sample_weights).
        self.train_bug_ids = []
        for bug, label in super().items_gen(classes):
            self.train_bug_ids.append(bug["id"])
            yield bug, label

    def get_sample_weights(self, X, y):
        if self.fixed_defects_weight == 1.0:
            return None

        bug_ids = np.array(self.train_bug_ids)[np.asarray(X.index)]
        return np.where(
            np.isin(bug_ids, list(self.fixed_defect_bug_ids)),
            self.fixed_defects_weight,
            1.0,
        )

    def get_bugbug_labels(self, kind="bug") -> dict[int, Any]:
        assert kind in ["bug", "regression", "defect_enhancement_task"]

        classes: dict[int, Any] = {}

        for bug_id, category in labels.get_labels("bug_nobug"):
            assert category in ["True", "False"], f"unexpected category {category}"
            if kind == "bug":
                classes[int(bug_id)] = 1 if category == "True" else 0
            elif kind == "regression":
                if category == "False":
                    classes[int(bug_id)] = 0
            elif kind == "defect_enhancement_task" and category == "True":
                classes[int(bug_id)] = "defect"

        unknown_regression_bug_ids = set()
        for bug_id, category in labels.get_labels("regression_bug_nobug"):
            assert category in [
                "nobug",
                "bug_unknown_regression",
                "bug_no_regression",
                "regression",
            ], f"unexpected category {category}"
            if kind == "bug":
                classes[int(bug_id)] = 1 if category != "nobug" else 0
            elif kind == "regression":
                if category == "bug_unknown_regression":
                    unknown_regression_bug_ids.add(int(bug_id))
                    continue

                classes[int(bug_id)] = 1 if category == "regression" else 0
            elif kind == "defect_enhancement_task" and category != "nobug":
                classes[int(bug_id)] = "defect"

        defect_enhancement_task_e = dict(labels.get_labels("defect_enhancement_task_e"))
        defect_enhancement_task_p = dict(labels.get_labels("defect_enhancement_task_p"))
        defect_enhancement_task_s = dict(labels.get_labels("defect_enhancement_task_s"))
        defect_enhancement_task_h = dict(labels.get_labels("defect_enhancement_task_h"))

        defect_enhancement_task_common = (
            (bug_id, category)
            for bug_id, category in defect_enhancement_task_p.items()
            if (
                bug_id not in defect_enhancement_task_e
                or defect_enhancement_task_e[bug_id]
                == defect_enhancement_task_p[bug_id]
            )
            and (
                bug_id not in defect_enhancement_task_s
                or defect_enhancement_task_s[bug_id]
                == defect_enhancement_task_p[bug_id]
            )
            and (
                bug_id not in defect_enhancement_task_h
                or defect_enhancement_task_h[bug_id]
                == defect_enhancement_task_p[bug_id]
            )
        )

        for bug_id, category in itertools.chain(
            labels.get_labels("defect_enhancement_task"), defect_enhancement_task_common
        ):
            assert category in ["defect", "enhancement", "task"]
            if kind == "bug":
                classes[int(bug_id)] = 1 if category == "defect" else 0
            elif kind == "regression":
                if category in ["enhancement", "task"]:
                    classes[int(bug_id)] = 0
            elif kind == "defect_enhancement_task":
                classes[int(bug_id)] = category

        # Augment labes by using bugs marked as 'regression' or 'feature', as they are basically labelled.
        # And also use the new bug type field.
        non_regression_max_creation_time = (
            datetime.now(timezone.utc) - NON_REGRESSION_MIN_AGE
        )
        self.fixed_defect_bug_ids = set()
        bug_ids = set()
        for bug in bugzilla.get_bugs():
            bug_id = int(bug["id"])

            bug_ids.add(bug_id)

            # Ignore meta bugs, their types are not consistently set.
            if "meta" in bug["keywords"]:
                if bug_id in classes:
                    del classes[bug_id]
                continue

            if bug_id in classes:
                continue

            if (
                len(bug["regressed_by"]) > 0
                or any(
                    keyword in bug["keywords"]
                    for keyword in ["regression", "talos-regression"]
                )
                or (
                    "cf_has_regression_range" in bug
                    and bug["cf_has_regression_range"] == "yes"
                )
            ):
                if kind in ["bug", "regression"]:
                    classes[bug_id] = 1
                else:
                    classes[bug_id] = "defect"
            elif any(keyword in bug["keywords"] for keyword in ["feature"]):
                if kind in ["bug", "regression"]:
                    classes[bug_id] = 0
                else:
                    classes[bug_id] = "enhancement"
            elif kind == "regression":
                for history in bug["history"]:
                    for change in history["changes"]:
                        if change["field_name"] == "keywords":
                            if "regression" in [
                                k.strip() for k in change["removed"].split(",")
                            ]:
                                classes[bug_id] = 0
                            elif "regression" in [
                                k.strip() for k in change["added"].split(",")
                            ]:
                                classes[bug_id] = 1

            # The conditions to use the 'defect' type are more restricted.
            can_use_type = False
            can_use_defect_type = False

            # We can use the type as a label for all bugs after the migration (https://bugzilla.mozilla.org/show_bug.cgi?id=1524738), if they are not defects.
            if bug_id > 1540807:
                can_use_type = True

            # And we can use the type as a label for bugs whose type has been modified.
            # For 'defects', we can't use them as labels unless resulting from a change, because bugs are filed by default as 'defect' and so they could be mistakes.
            def _has_type_changed(bug):
                for history in bug["history"]:
                    for change in history["changes"]:
                        if change["field_name"] == "type":
                            return True

                return False

            if not can_use_type or bug["type"] == "defect":
                can_use_type = can_use_defect_type = _has_type_changed(bug)

            if can_use_type:
                if bug["type"] == "enhancement":
                    if kind == "bug" or kind == "regression":
                        classes[bug_id] = 0
                    elif kind == "defect_enhancement_task":
                        classes[bug_id] = "enhancement"
                elif bug["type"] == "task":
                    if kind == "bug" or kind == "regression":
                        classes[bug_id] = 0
                    elif kind == "defect_enhancement_task":
                        classes[bug_id] = "task"
                elif bug["type"] == "defect" and can_use_defect_type:
                    if kind == "bug":
                        classes[bug_id] = 1
                    elif kind == "defect_enhancement_task":
                        classes[bug_id] = "defect"

            # Defects which were fixed without being identified as regressions are
            # most likely defects which are not regressions. Without them, almost all
            # defects in the training set would be regressions (as the 'defect' type
            # is only used when it was explicitly set), while the models are mostly
            # applied to defects which are not regressions.
            if (
                bug_id not in classes
                and bug_id not in unknown_regression_bug_ids
                and bug_id > 1540807
                and bug["type"] == "defect"
                and bug["resolution"] == "FIXED"
                and dateutil.parser.parse(bug["creation_time"])
                < non_regression_max_creation_time
            ):
                if kind == "bug":
                    classes[bug_id] = 1
                elif kind == "regression":
                    classes[bug_id] = 0
                elif kind == "defect_enhancement_task":
                    classes[bug_id] = "defect"
                self.fixed_defect_bug_ids.add(bug_id)

        # Remove labels which belong to bugs for which we have no data.
        return {bug_id: label for bug_id, label in classes.items() if bug_id in bug_ids}

    def get_labels(self) -> tuple[dict[int, Any], list[Any]]:
        classes = self.get_bugbug_labels("bug")

        logger.info("%d bugs", (sum(label == 1 for label in classes.values())))
        logger.info("%d non-bugs", (sum(label == 0 for label in classes.values())))

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if (
                any(
                    keyword in bug["keywords"]
                    for keyword in ["regression", "talos-regression"]
                )
                or (
                    "cf_has_regression_range" in bug
                    and bug["cf_has_regression_range"] == "yes"
                )
                or len(bug["regressed_by"]) > 0
            ):
                classes[i] = 1 if not probabilities else [0.0, 1.0]
            elif "feature" in bug["keywords"]:
                classes[i] = 0 if not probabilities else [1.0, 0.0]

        return classes
