# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os

from bugbug import labels


def test_get_labels_dir():
    path = labels.get_labels_dir()
    assert os.path.isabs(path)
    assert path.endswith("labels")


def test_get_all_bug_ids():
    labels.get_all_bug_ids()
