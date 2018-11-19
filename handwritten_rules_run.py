# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import bug_rules
from bugbug import bugzilla
from bugbug import labels

classes = labels.get_labels()

true_positives = 0
true_negatives = 0
false_positives = 0
false_negatives = 0

for bug in bugzilla.get_bugs():
    bug_id = bug['id']

    if bug_id not in classes:
        continue

    is_bug = classes[bug_id]

    is_bug_pred = bug_rules.is_bug(bug)

    if is_bug_pred and is_bug:
        true_positives += 1
    elif not is_bug_pred and not is_bug:
        true_negatives += 1
    elif is_bug_pred and not is_bug:
        false_positives += 1
    elif not is_bug_pred and is_bug:
        false_negatives += 1

print('Accuracy: {:.3%}'.format(float(true_positives + true_negatives) / len(classes)))
print('Precision: {:.3%}'.format(float(true_positives) / (true_positives + false_positives)))
print('Recall: {:.3%}'.format(float(true_positives) / (true_positives + false_negatives)))
print('Specificity: {:.3%}'.format(float(true_negatives) / (true_negatives + false_positives)))
