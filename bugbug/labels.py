# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import csv
import os
import sys


def get_labels_dir():
    return os.path.join(os.path.dirname(sys.modules[__package__].__file__), "labels")


def get_labels(file_name):
    with open(os.path.join(get_labels_dir(), f"{file_name}.csv"), "r") as f:
        reader = csv.reader(f)
        next(reader)
        yield from reader


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
