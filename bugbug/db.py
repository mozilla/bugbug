# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import lzma
import os
import shutil
from datetime import datetime
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

            ver_path = f'{path}_version.txt'
            with open(ver_path, 'w') as vf:
                vf.write(str(datetime.now()))

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


def delete(path, match):
    assert path in DATABASES

    with open(f'{path}_new', 'w') as fw:
        with open(path, 'r') as fr:
            for line in fr:
                elem = json.loads(line)
                if not match(elem):
                    fw.write(line)
                    fw.write('\n')

    os.unlink(path)
    os.rename(f'{path}_new', path)
