# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import csv
import os
import sys

from bugbug import bugzilla


def get_labels_dir():
    return os.path.join(os.path.dirname(sys.modules[__package__].__file__), 'labels')


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


def get_qa_needed_labels():
    classes = {}

    for bug_data in bugzilla.get_bugs():
        bug_id = int(bug_data['id'])

        for entry in bug_data['history']:
            for change in entry['changes']:
                if change['added'].startswith('qawanted'):
                    classes[bug_id] = True
                elif 'flags' in entry:
                    for flag in entry['flags']:
                        if flag['name'].startswith('qe-verify'):
                            classes[bug_id] = True

        if bug_id not in classes:
            classes[bug_id] = False

    return classes


def get_bugbug_labels(kind='bug', augmentation=False):
    assert kind in ['bug', 'regression']

    classes = {}

    with open(os.path.join(get_labels_dir(), 'bug_nobug.csv'), 'r') as f:
        reader = csv.reader(f)
        next(reader)
        for bug_id, category in reader:
            assert category in ['True', 'False'], 'unexpected category {}'.format(category)
            if kind == 'bug':
                classes[int(bug_id)] = True if category == 'True' else False
            elif kind == 'regression':
                if category == 'False':
                    classes[int(bug_id)] = False

    with open(os.path.join(get_labels_dir(), 'regression_bug_nobug.csv'), 'r') as f:
        reader = csv.reader(f)
        next(reader)
        for bug_id, category in reader:
            assert category in ['nobug', 'bug_unknown_regression', 'bug_no_regression', 'regression'], 'unexpected category {}'.format(category)
            if kind == 'bug':
                classes[int(bug_id)] = True if category != 'nobug' else False
            elif kind == 'regression':
                if category == 'bug_unknown_regression':
                    continue

                classes[int(bug_id)] = True if category == 'regression' else False

    bug_ids = set()
    for bug in bugzilla.get_bugs():
        bug_id = int(bug['id'])

        bug_ids.add(bug_id)

        if bug_id in classes:
            continue

        # If augmentation is enabled, use bugs marked as 'regression' or 'feature',
        # as they are basically labelled.
        if not augmentation:
            continue

            if any(keyword in bug['keywords'] for keyword in ['regression', 'talos-regression']) or ('cf_has_regression_range' in bug and bug['cf_has_regression_range'] == 'yes'):
                classes[bug_id] = True
            elif any(keyword in bug['keywords'] for keyword in ['feature']):
                classes[bug_id] = False

    # Remove labels which belong to bugs for which we have no data.
    return {bug_id: label for bug_id, label in classes.items() if bug_id in bug_ids}


def get_uplift_labels():
    classes = {}

    for bug_data in bugzilla.get_bugs():
        bug_id = int(bug_data['id'])

        for attachment in bug_data['attachments']:
            for flag in attachment['flags']:
                if not flag['name'].startswith('approval-mozilla-') or flag['status'] not in ['+', '-']:
                    continue

                if flag['status'] == '+':
                    classes[bug_id] = True
                elif flag['status'] == '-':
                    classes[bug_id] = False

    return classes


def get_all_bug_ids():
    bug_ids = set()

    labels_dir = get_labels_dir()
    for csv_file in os.listdir(labels_dir):
        with open(os.path.join(labels_dir, csv_file)) as f:
            reader = csv.reader(f)
            # Assume the first row is the header.
            next(reader)
            # Assume the first column is the bug ID.
            bug_ids.update([row[0] for row in reader])

    return list(bug_ids)
