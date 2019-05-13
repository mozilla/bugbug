# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.defect import DefectModel


class RegressionModel(DefectModel):
    def __init__(self, lemmatization=False):
        DefectModel.__init__(self, lemmatization)

    def get_labels(self):
        return self.get_bugbug_labels("regression"), [0, 1]
