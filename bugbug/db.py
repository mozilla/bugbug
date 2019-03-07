# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip
import json
import lzma
import os
import shutil
from contextlib import contextmanager
from urllib.request import urlretrieve

DATABASES = {}

VER_PATH = 'data/DB_VERSION.json'


def register(path, url, version):
    DATABASES[path] = {'url': url, 'version': version}

    # Create DB parent directory.
    parent_dir = os.path.dirname(path)
    if not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)


# Download and extract databases.
def download():
    for path, info in DATABASES.items():
        if os.path.exists(path) and not is_outdated(path):
            continue

        xz_path = f'{path}.xz'

        # Only download if the xz file is not there yet.
        if not os.path.exists(xz_path) or is_outdated(path):
            urlretrieve(DATABASES[path]['url'], xz_path)

        with open(path, 'wb') as output_f:
            with lzma.open(xz_path) as input_f:
                shutil.copyfileobj(input_f, output_f)

        update_ver_file(path)


@contextmanager
def _db_open(path, mode):
    _, ext = os.path.splitext(path)

    if ext == '.gz':
        with gzip.GzipFile(path, mode) as f:
            yield f
    else:
        with open(path, mode) as f:
            yield f


def read(path):
    assert path in DATABASES

    if not os.path.exists(path):
        return ()

    with _db_open(path, 'r') as f:
        for line in f:
            yield json.loads(line)


def _fwrite(f, elems):
    for elem in elems:
        f.write((json.dumps(elem) + '\n').encode('utf-8'))


def write(path, elems):
    assert path in DATABASES

    with _db_open(path, 'wb') as f:
        _fwrite(f, elems)


def append(path, elems):
    assert path in DATABASES

    with _db_open(path, 'ab') as f:
        _fwrite(f, elems)


def delete(path, match):
    assert path in DATABASES

    dirname, basename = os.path.split(path)
    new_path = os.path.join(dirname, f'new_{basename}')

    with _db_open(new_path, 'w') as fw:
        with _db_open(path, 'r') as fr:
            for line in fr:
                elem = json.loads(line)
                if not match(elem):
                    fw.write(line)

    os.unlink(path)
    os.rename(new_path, path)


def is_outdated(path):
    if not os.path.exists(VER_PATH):
        return True

    with open(VER_PATH) as db:
        ver = json.load(db)
    return DATABASES[path]['version'] != ver[path] if path in ver else True


def update_ver_file(db_path):
    if os.path.exists(VER_PATH):
        with open(VER_PATH) as db:
            ver_dict = json.load(db)
    else:
        ver_dict = {}

    ver_dict[db_path] = DATABASES[db_path]['version']

    with open(VER_PATH, 'w') as db:
        json.dump(ver_dict, db)
