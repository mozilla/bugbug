# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import db


def test_write_read(tmp_path):
    db_path = tmp_path / 'prova.json'

    db.register(db_path, 'https://alink', 1)

    db.write(db_path, range(1, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_write_read_compressed(tmp_path):
    db_path = tmp_path / 'prova.json.gz'

    db.register(db_path, 'https://alink', 1)

    db.write(db_path, range(1, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_append(tmp_path):
    db_path = tmp_path / 'prova.json'

    db.register(db_path, 'https://alink', 1)

    db.write(db_path, range(1, 4))

    assert list(db.read(db_path)) == [1, 2, 3]

    db.append(db_path, range(4, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_append_compressed(tmp_path):
    db_path = tmp_path / 'prova.json.gz'

    db.register(db_path, 'https://alink', 1)

    db.write(db_path, range(1, 4))

    assert list(db.read(db_path)) == [1, 2, 3]

    db.append(db_path, range(4, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_delete(tmp_path):
    db_path = tmp_path / 'prova.json'

    print(db_path)

    db.register(db_path, 'https://alink', 1)

    db.write(db_path, range(1, 9))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7, 8]

    db.delete(db_path, lambda x: x == 4)

    assert list(db.read(db_path)) == [1, 2, 3, 5, 6, 7, 8]


def test_delete_compressed(tmp_path):
    db_path = tmp_path / 'prova.json.gz'

    print(db_path)

    db.register(db_path, 'https://alink', 1)

    db.write(db_path, range(1, 9))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7, 8]

    db.delete(db_path, lambda x: x == 4)

    assert list(db.read(db_path)) == [1, 2, 3, 5, 6, 7, 8]
