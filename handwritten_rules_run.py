# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import bugbug
from get_bugs import get_bugs
from get_bugs import get_labels

classes = get_labels()

bugs = get_bugs()

true_positives = 0
true_negatives = 0
false_positives = 0
false_negatives = 0

for bug_id, is_bug in classes.items():
    if bug_id not in bugs:
        continue

    is_bug_pred = bugbug.is_bug(bugs[bug_id])

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
