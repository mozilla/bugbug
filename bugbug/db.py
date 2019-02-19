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


def register(path, url, version):
    DATABASES[path] = {'url': url, 'version': version}

    # Create DB parent directory.
    parent_dir = os.path.dirname(path)
    if not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)


# Download and extract databases.
def download():
    for path, info in DATABASES.items():
        if os.path.exists(path) and not is_updated_ver(path):
            continue

        xz_path = f'{path}.xz'

        ver_path = os.path.join(os.path.dirname(path), 'DB_VERSION.json')

        # Only download if the xz file is not there yet.
        if not os.path.exists(xz_path) or is_updated_ver(path):
            urlretrieve(DATABASES[path]['url'], xz_path)

        with open(path, 'wb') as output_f:
            with lzma.open(xz_path) as input_f:
                shutil.copyfileobj(input_f, output_f)

        if not os.path.exists(ver_path):
            ver_dict = {}
            with open(ver_path, 'w') as db:
                ver_dict[path] = info['version']
                json.dump(ver_dict, db)
        else:
            update_ver_file(ver_path, path)


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


def is_updated_ver(db_path):
    path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ver_path = os.path.join(path, 'data', 'DB_VERSION.json')

    if not os.path.exists(ver_path):
        return True
    else:
        with open(ver_path) as db:
            ver = json.load(db)
        if ver.get(db_path):
            return not DATABASES[db_path]['version'] == ver[db_path]
        else:
            return True


def update_ver_file(ver_path, db_path):
    with open(ver_path) as db:
        ver_dict = json.load(db)

    ver_dict[db_path] = DATABASES[db_path]['version']
    with open(ver_path, 'w') as db:
        json.dump(ver_dict, db)
