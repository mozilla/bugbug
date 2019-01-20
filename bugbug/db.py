# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import lzma
import os
import shutil
from urllib.request import urlretrieve

DATABASES = {}


def register(path, url):
    DATABASES[path] = url

    # Create DB parent directory.
    parent_dir = os.path.dirname(path)
    if not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)


# Download and extract databases.
def download():
    for path, url in DATABASES.items():
        if os.path.exists(path):
            continue

        xz_path = f'{path}.xz'

        # Only download if the xz file is not there yet.
        if not os.path.exists(xz_path):
            urlretrieve(DATABASES[path], xz_path)

        with open(path, 'wb') as output_f:
            with lzma.open(xz_path) as input_f:
                shutil.copyfileobj(input_f, output_f)


def read(path):
    assert path in DATABASES

    if not os.path.exists(path):
        return ()

    with open(path, 'r') as f:
        for line in f:
            yield json.loads(line)


def write(path, bugs):
    assert path in DATABASES

    with open(path, 'w') as f:
        for bug in bugs:
            f.write(json.dumps(bug))
            f.write('\n')


def append(path, bugs):
    assert path in DATABASES

    with open(path, 'a') as f:
        for bug in bugs:
            f.write(json.dumps(bug))
            f.write('\n')
