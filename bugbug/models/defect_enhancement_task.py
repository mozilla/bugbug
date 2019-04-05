# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.bug import BugModel


class DefectEnhancementTaskModel(BugModel):
    def __init__(self, lemmatization=False):
        BugModel.__init__(self, lemmatization)

    def get_labels(self):
        classes = self.get_bugbug_labels('defect_enhancement_task')

        print('{} defects'.format(sum(1 for label in classes.values() if label == 'd')))
        print('{} enhancements'.format(sum(1 for label in classes.values() if label == 'e')))
        print('{} tasks'.format(sum(1 for label in classes.values() if label == 't')))

        return classes

    def overwrite_classes(self, bugs, classes, probabilities):
        for i, bug in enumerate(bugs):
            if any(keyword in bug['keywords'] for keyword in ['regression', 'talos-regression']) or ('cf_has_regression_range' in bug and bug['cf_has_regression_range'] == 'yes'):
                classes[i] = 'd' if not probabilities else [1., 0., 0.]
            elif 'feature' in bug['keywords']:
                classes[i] = 'e' if not probabilities else [0., 1., 0.]

        return classes
