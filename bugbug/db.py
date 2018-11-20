# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import lzma
import os


def read(path):
    if not os.path.exists(path) and os.path.exists('{}.xz'.format(path)):
        # A compressed database exists, extract it.
        with open(path, 'wb') as output_f:
            with lzma.open('{}.xz'.format(path)) as input_f:
                output_f.write(input_f.read())

    if not os.path.exists(path):
        raise Exception('Database {} does not exist.'.format(path))

    with open(path, 'r') as f:
        for line in f:
            yield json.loads(line)


def write(path, bugs):
    with open(path, 'w') as f:
        for bug in bugs:
            f.write(json.dumps(bug))
            f.write('\n')


def append(path, bugs):
    with open(path, 'a') as f:
        for bug in bugs:
            f.write(json.dumps(bug))
            f.write('\n')
