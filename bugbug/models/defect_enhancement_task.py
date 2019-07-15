# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.defect import DefectModel


class DefectEnhancementTaskModel(DefectModel):
    def __init__(self, lemmatization=False, historical=False):
        DefectModel.__init__(self, lemmatization, historical)

        self.calculate_importance = False

    def get_labels(self):
        classes = self.get_bugbug_labels("defect_enhancement_task")

        print(
            "{} defects".format(
                sum(1 for label in classes.values() if label == "defect")
            )
        )
        print(
            "{} enhancements".format(
                sum(1 for label in classes.values() if label == "enhancement")
            )
        )
        print(
            "{} tasks".format(sum(1 for label in classes.values() if label == "task"))
        )

        return classes, ["defect", "enhancement", "task"]

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
                classes[i] = "defect" if not probabilities else [1.0, 0.0, 0.0]
            elif "feature" in bug["keywords"]:
                classes[i] = "enhancement" if not probabilities else [0.0, 1.0, 0.0]

        return classes

    def get_extra_data(self):

        labels = self.clf._le.inverse_transform([0, 1, 2])
        labels_map = {label: index for label, index in zip(labels, [0, 1, 2])}

        return {"labels_map": labels_map}
