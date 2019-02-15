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
        if os.path.exists(path) and not is_updated_ver():
            continue

        xz_path = f'{path}.xz'

        # Only download if the xz file is not there yet.
        if not os.path.exists(xz_path) or is_updated_ver():
            urlretrieve(DATABASES[path], xz_path)

            loc_ver_path = os.path.join(os.path.dirname(path), 'loc_DB_VERSION.json')
            db_ver_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'DB_VERSION.json')

            if not os.path.exists(loc_ver_path):
                with open(db_ver_path) as db_ver:
                    with open(loc_ver_path, 'w') as loc_db_ver:
                        json.dump(json.load(db_ver), loc_db_ver)
            else:
                update_ver_file(db_ver_path, loc_ver_path, os.path.basename(path))

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


def is_updated_ver():
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    loc_ver_path = os.path.join(repo_dir, 'data', 'loc_DB_VERSION.json')
    db_ver_path = os.path.join(repo_dir, 'DB_VERSION.json')

    if not os.path.exists(loc_ver_path):
        return True
    else:
        with open(db_ver_path) as db_ver:
            ver = json.load(db_ver)

        with open(loc_ver_path) as loc_db_ver:
            loc_ver = json.load(loc_db_ver)
        return not ver == loc_ver


def update_ver_file(db_ver_path, loc_ver_path, db):
    with open(db_ver_path) as db_ver:
        ver = json.load(db_ver)

    with open(loc_ver_path) as loc_db_ver:
        loc_ver = json.load(loc_db_ver)

    loc_ver[db] = ver[db]
    with open(loc_ver_path, 'w') as loc_db_ver:
        json.dump(loc_ver, loc_db_ver)
