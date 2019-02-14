# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.bug import BugModel


class DefectFeatureTaskModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

    def get_labels(self):
        classes = self.get_bugbug_labels('defect_feature_task')

        print('{} defects'.format(sum(1 for label in classes.values() if label == 'd')))
        print('{} features'.format(sum(1 for label in classes.values() if label == 'f')))
        print('{} tasks'.format(sum(1 for label in classes.values() if label == 't')))

        return classes
