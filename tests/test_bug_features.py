# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os

from bugbug.bug_features import has_str


def read(path):
    check = has_str()
    if not os.path.exists(path):
        return ()

    data = []
    with open(path, 'r') as f:
        for line in f:
            data.append(check(json.loads(line)))

    return data


def test_has_str():

    bugs = read('tests/data/test_bugs.json')
    results = ['yes', None, None, 'yes']
    assert results == bugs
