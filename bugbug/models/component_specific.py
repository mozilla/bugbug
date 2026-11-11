# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from datetime import datetime, timezone

import dateutil.parser
import xgboost
from dateutil.relativedelta import relativedelta
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction import DictVectorizer
from sklearn.pipeline import Pipeline

from bugbug import bug_features, bugzilla, feature_cleanup, utils
from bugbug.model import BugModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT = "release-mgmt-account-bot@mozilla.tld"


def get_product_component_history(bug):
    """Return the (product, component, when it was set, who set it) the bug had over time."""
    changes = []
    for history in bug["history"]:
        product = component = None
        for change in history["changes"]:
            if change["field_name"] == "product":
                product = (change["removed"], change["added"])
            elif change["field_name"] == "component":
                component = (change["removed"], change["added"])

        if product is not None or component is not None:
            changes.append((history["when"], history["who"], product, component))

    initial_product = bug["product"]
    initial_component = bug["component"]
    for _, _, product, component in reversed(changes):
        if product is not None:
            initial_product = product[0]
        if component is not None:
            initial_component = component[0]

    states = [
        (initial_product, initial_component, bug["creation_time"], bug["creator"])
    ]
    for when, who, product, component in changes:
        states.append(
            (
                product[1] if product is not None else states[-1][0],
                component[1] if component is not None else states[-1][1],
                when,
                who,
            )
        )

    return states


class ComponentSpecificModel(BugModel):
    """Predict whether a bug in a generic component should be moved out of it.

    bugbot uses it to decide whether to move bugs out of Firefox::General, when the
    component model suggests a component for them.
    """

    def __init__(self, lemmatization=False, product="Firefox", component="General"):
        BugModel.__init__(self, lemmatization)

        self.product = product
        self.component = component

        # SHAP doesn't support the calibrated classifier.
        self.calculate_importance = False

        feature_extractors = [
            bug_features.HasSTR(),
            bug_features.Severity(),
            bug_features.Keywords(),
            bug_features.HasCrashSignature(),
            bug_features.HasURL(),
            bug_features.HasW3CURL(),
            bug_features.HasGithubURL(),
            bug_features.Whiteboard(),
            bug_features.Patches(),
            bug_features.Landings(),
        ]

        cleanup_functions = [
            feature_cleanup.fileref(),
            feature_cleanup.url(),
            feature_cleanup.synonyms(),
        ]

        self.extraction_pipeline = Pipeline(
            [
                (
                    "bug_extractor",
                    bug_features.BugExtractor(
                        feature_extractors, cleanup_functions, rollback=True
                    ),
                ),
            ]
        )

        self.clf = Pipeline(
            [
                (
                    "union",
                    ColumnTransformer(
                        [
                            ("data", DictVectorizer(), "data"),
                            ("title", self.text_vectorizer(min_df=0.0001), "title"),
                            (
                                "comments",
                                self.text_vectorizer(min_df=0.0001),
                                "comments",
                            ),
                        ]
                    ),
                ),
                (
                    "estimator",
                    # bugbug uses a fixed threshold on the probability, so calibrate it.
                    CalibratedClassifierCV(
                        xgboost.XGBClassifier(n_jobs=utils.get_physical_cpu_count()),
                        method="isotonic",
                        cv=3,
                        ensemble=False,
                    ),
                ),
            ]
        )

    def was_in_component_at(self, states, when):
        current = None
        for product, component, set_when, _ in states:
            if set_when <= when:
                current = (product, component)
        return current == (self.product, self.component)

    def was_fixed_in_component(self, bug, states):
        """Whether the bug was fixed while it was in the component."""
        if bug["resolution"] != "FIXED":
            return False

        fixed_when = [
            history["when"]
            for history in bug["history"]
            for change in history["changes"]
            if change["field_name"] == "resolution" and change["added"] == "FIXED"
        ]
        return bool(fixed_when) and self.was_in_component_at(states, fixed_when[-1])

    def was_triaged_in_component(self, bug, states):
        """Whether someone set the severity of the bug while it was in the component."""
        if bug["severity"] in ("--", ""):
            return False

        severity_changes = [
            (history["when"], history["who"])
            for history in bug["history"]
            for change in history["changes"]
            if change["field_name"] == "severity" and change["added"] not in ("--", "")
        ]

        # The severity was set when the bug was filed.
        if not severity_changes:
            return states[0][:2] == (self.product, self.component)

        return any(
            who != BOT and self.was_in_component_at(states, when)
            for when, who in severity_changes
        )

    def get_labels(self):
        classes = {}

        now = datetime.now(timezone.utc)

        for bug_data in bugzilla.get_bugs():
            if dateutil.parser.parse(bug_data["creation_time"]) < now - relativedelta(
                years=3
            ):
                continue

            states = get_product_component_history(bug_data)

            def is_target(state):
                return state[0] == self.product and state[1] == self.component

            target_states = [state for state in states if is_target(state)]
            if not target_states:
                continue

            # bugbot never classifies bugs it moved itself (e.g. the Firefox::Untriaged
            # bugs it moves to Firefox::General when it isn't confident enough).
            if target_states[0][3] == BOT:
                continue

            moved_out_by = [
                states[i][3]
                for i in range(1, len(states))
                if is_target(states[i - 1]) and not is_target(states[i])
            ]

            if not is_target(states[-1]):
                # The bug was moved out to a more specific component. Moves by the bot
                # that nobody corrected are almost always right.
                classes[bug_data["id"]] = 1
            elif BOT in moved_out_by:
                # The bot moved the bug out, but someone moved it back.
                classes[bug_data["id"]] = 0
            elif self.was_fixed_in_component(bug_data, states):
                # The bug was fixed while in the component.
                classes[bug_data["id"]] = 0
            elif dateutil.parser.parse(states[-1][2]) < now - relativedelta(
                days=90
            ) and self.was_triaged_in_component(bug_data, states):
                # The bug has been in the component for a while and someone triaged it
                # there, nobody felt the need to move it.
                classes[bug_data["id"]] = 0
            # Otherwise, we don't know whether the bug belongs in the component: it was
            # put there recently, or it was never triaged (it might just be neglected).

        logger.info(
            "%d bugs were moved out of %s::%s",
            sum(label == 1 for label in classes.values()),
            self.product,
            self.component,
        )
        logger.info(
            "%d bugs belong in %s::%s",
            sum(label == 0 for label in classes.values()),
            self.product,
            self.component,
        )

        return classes, [0, 1]

    def get_feature_names(self):
        return self.clf.named_steps["union"].get_feature_names_out()
