# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import csv

from bugbug import bugzilla


def get_tracking_labels():
    classes = {}

    for bug_data in bugzilla.get_bugs():
        bug_id = int(bug_data['id'])

        for entry in bug_data['history']:
            for change in entry['changes']:
                if change['field_name'].startswith('cf_tracking_firefox'):
                    if change['added'] in ['blocking', '+']:
                        classes[bug_id] = True
                    elif change['added'] == '-':
                        classes[bug_id] = False

        if bug_id not in classes:
            classes[bug_id] = False

    return classes


def get_bugbug_labels(augmentation=False):
    with open('labels/bug_nobug.csv', 'r') as f:
        classes = dict([row for row in csv.reader(f)][1:])

    with open('labels/regression_bug_nobug.csv', 'r') as f:
        classes_more = [row for row in csv.reader(f)][1:]

    for bug_id, category in classes_more:
        if category == 'nobug':
            is_bug = 'False'
        else:
            is_bug = 'True'

        classes[bug_id] = is_bug

    for bug_id, is_bug in classes.items():
        assert is_bug == 'True' or is_bug == 'False'

    # Turn bug IDs into integers and labels into booleans.
    classes = {int(bug_id): True if label == 'True' else False for bug_id, label in classes.items()}

    if augmentation:
        # Use bugs marked as 'regression' or 'feature', as they are basically labelled.
        bug_ids = set()
        for bug in bugzilla.get_bugs():
            bug_id = int(bug['id'])

            bug_ids.add(bug_id)

            if bug_id in classes:
                continue

            if any(keyword in bug['keywords'] for keyword in ['regression', 'talos-regression']) or ('cf_has_regression_range' in bug and bug['cf_has_regression_range'] == 'yes'):
                classes[bug_id] = True
            elif any(keyword in bug['keywords'] for keyword in ['feature']):
                classes[bug_id] = False

        # Remove labels which belong to bugs for which we have no data.
        classes = {bug_id: label for bug_id, label in classes.items() if bug_id in bug_ids}

    return classes


if __name__ == '__main__':
    classes = get_bugbug_labels(augmentation=False)
    bugzilla.download_bugs([bug_id for bug_id in classes.keys()])
