# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import random
from datetime import datetime, timedelta
from typing import Any

import dateutil.parser

from bugbug.models.defect import DefectModel, get_regression_label_date

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RegressionModel(DefectModel):
    def __init__(self, lemmatization=False, historical=False):
        DefectModel.__init__(self, lemmatization, historical)
        self.calculate_importance = False

        # The time it took to identify the training regressions as such (see
        # get_rollback_point).
        self.regression_label_delays: list[timedelta] = []
        self.random = random.Random(0)

    def get_labels(self) -> tuple[dict[int, Any], list[int]]:
        classes = self.get_bugbug_labels("regression")

        logger.info("%d regression bugs", sum(label == 1 for label in classes.values()))
        logger.info(
            "%d non-regression bugs", sum(label == 0 for label in classes.values())
        )

        return classes, [0, 1]

    def get_rollback_point(self, item: dict, label: Any) -> datetime:
        # The model is applied to defects at any point of their life, until they are
        # identified as regressions. So regressions are rolled back to right before
        # they were identified as such, and non-regressions are rolled back to a
        # similar age, to avoid the model telling them apart from their age.
        creation_date = dateutil.parser.parse(item["creation_time"])
        regression_label_date = get_regression_label_date(item)
        if label == 1 and regression_label_date is not None:
            self.regression_label_delays.append(regression_label_date - creation_date)
            return regression_label_date

        if label == 0 and self.regression_label_delays:
            return creation_date + self.random.choice(self.regression_label_delays)

        return super().get_rollback_point(item, label)

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            regression_keyword_removed = False
            for history in bug["history"]:
                for change in history["changes"]:
                    if change["field_name"] == "keywords":
                        if "regression" in [
                            k.strip() for k in change["removed"].split(",")
                        ]:
                            regression_keyword_removed = True
                        elif "regression" in [
                            k.strip() for k in change["added"].split(",")
                        ]:
                            regression_keyword_removed = False

            if regression_keyword_removed:
                classes[i] = 0 if not probabilities else [1.0, 0.0]

        super().overwrite_classes(bugs, classes, probabilities)

        return classes
