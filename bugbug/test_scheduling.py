# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import db

TEST_SCHEDULING_DB = "data/test_scheduling_history.pickle"
db.register(
    TEST_SCHEDULING_DB,
    "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_test_scheduling_history.latest/artifacts/public/test_scheduling_history.pickle.zst",
    2,
    ["past_failures.shelve.db.zst"],
)


def get_test_scheduling_history():
    return db.read(TEST_SCHEDULING_DB)
