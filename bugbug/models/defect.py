# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools
from typing import Any, Dict, List, Tuple

import xgboost
from imblearn.over_sampling import BorderlineSMOTE
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, labels, utils
from bugbug.model import BugModel


class DefectModel(BugModel):
    def __init__(self, lemmatization=False, historical=False):
        BugModel.__init__(self, lemmatization)

        self.sampler = BorderlineSMOTE(random_state=0)

        feature_extractors = [
            bug_features.has_str(),
            bug_features.severity(),
            # Ignore keywords that would make the ML completely skewed
            # (we are going to use them as 100% rules in the evaluation phase).
            bug_features.keywords({"regression", "talos-regression", "feature"}),
            bug_features.is_coverity_issue(),
            bug_features.has_crash_signature(),
            bug_features.has_url(),
            bug_features.has_w3c_url(),
            bug_features.has_github_url(),
            bug_features.whiteboard(),
            bug_features.blocked_bugs_number(),
            bug_features.ever_affected(),
            bug_features.affected_then_unaffected(),
            bug_features.product(),
            bug_features.component(),
        ]

        if historical:
            feature_extractors += [
                bug_features.had_severity_enhancement(),
                bug_features.patches(),
                bug_features.landings(),
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
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(min_df=0.001), "title"),
                            (
                                "first_comment",
                                self.text_vectorizer(min_df=0.001),
                                "first_comment",
                            ),
                            (
                                "comments",
                                self.text_vectorizer(min_df=0.001),
                                "comments",
                            ),
                        ]
                    ),
                ),
            ]
        )

        self.clf = xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count())
        self.clf.set_params(predictor="cpu_predictor")

    def get_bugbug_labels(self, kind="bug") -> Dict[int, Any]:
        assert kind in ["bug", "regression", "defect_enhancement_task"]

        classes: Dict[int, Any] = {}

        for bug_id, category in labels.get_labels("bug_nobug"):
            assert category in ["True", "False"], f"unexpected category {category}"
            if kind == "bug":
                classes[int(bug_id)] = 1 if category == "True" else 0
            elif kind == "regression":
                if category == "False":
                    classes[int(bug_id)] = 0
            elif kind == "defect_enhancement_task":
                if category == "True":
                    classes[int(bug_id)] = "defect"

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
                    continue

                classes[int(bug_id)] = 1 if category == "regression" else 0
            elif kind == "defect_enhancement_task":
                if category != "nobug":
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
        bug_ids = set()
        for bug in bugzilla.get_bugs():
            bug_id = int(bug["id"])

            bug_ids.add(bug_id)

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
            if not can_use_type or bug["type"] == "defect":
                for history in bug["history"]:
                    for change in history["changes"]:
                        if change["field_name"] == "type":
                            can_use_type = can_use_defect_type = True

            if can_use_type:
                if bug["type"] == "enhancement":
                    if kind == "bug":
                        classes[bug_id] = 0
                    elif kind == "regression":
                        classes[bug_id] = 0
                    elif kind == "defect_enhancement_task":
                        classes[bug_id] = "enhancement"
                elif bug["type"] == "task":
                    if kind == "bug":
                        classes[bug_id] = 0
                    elif kind == "regression":
                        classes[bug_id] = 0
                    elif kind == "defect_enhancement_task":
                        classes[bug_id] = "task"
                elif bug["type"] == "defect" and can_use_defect_type:
                    if kind == "bug":
                        classes[bug_id] = 1
                    elif kind == "defect_enhancement_task":
                        classes[bug_id] = "defect"

        # Remove labels which belong to bugs for which we have no data.
        return {bug_id: label for bug_id, label in classes.items() if bug_id in bug_ids}

    def get_labels(self) -> Tuple[Dict[int, Any], List[Any]]:
        classes = self.get_bugbug_labels("bug")

        print("{} bugs".format(sum(1 for label in classes.values() if label == 1)))
        print("{} non-bugs".format(sum(1 for label in classes.values() if label == 0)))

        return classes, [0, 1]

    def get_feature_names(self):
        return self.extraction_pipeline.named_steps["union"].get_feature_names()

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
