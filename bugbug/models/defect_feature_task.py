# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import labels
from bugbug.models.bug import BugModel


class DefectFeatureTaskModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

        self.cross_validation_enabled = False

    def get_labels(self):
        classes = self.get_bugbug_labels('bug')

        classes = {bug_id: 'd' for bug_id, label in classes.items() if label == 1}

        for bug_id, label in labels.get_labels('defect_feature_task'):
            assert label in ['d', 'f', 't']
            classes[int(bug_id)] = label

        print('{} defects'.format(sum(1 for label in classes.values() if label == 'd')))
        print('{} features'.format(sum(1 for label in classes.values() if label == 'f')))
        print('{} tasks'.format(sum(1 for label in classes.values() if label == 't')))

        return classes
