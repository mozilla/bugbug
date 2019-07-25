# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import csv
import os
import sys

from bugbug import utils

LABELS_URLS = {
    "regressor": "https://github.com/marco-c/mozilla-central-regressors/raw/master/regressor.csv"
}


def get_labels_dir():
    return os.path.join(os.path.dirname(sys.modules[__package__].__file__), "labels")


def get_labels(file_name):
    path = os.path.join(get_labels_dir(), f"{file_name}.csv")

    if not os.path.exists(path) and file_name in LABELS_URLS:
        utils.download_check_etag(LABELS_URLS[file_name], path)

    with open(path, "r") as f:
        reader = csv.reader(f)
        next(reader)
        yield from reader


def get_all_bug_ids():
    bug_ids = set()

    labels_dir = get_labels_dir()
    for csv_file in os.listdir(labels_dir):
        with open(os.path.join(labels_dir, csv_file)) as f:
            reader = csv.DictReader(f)
            if "bug_id" not in reader.fieldnames:
                continue

            bug_ids.update([int(row["bug_id"]) for row in reader])

    return list(bug_ids)
