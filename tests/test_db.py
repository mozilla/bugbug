# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from bugbug import db


@pytest.fixture
def mock_db(tmp_path):
    def register_db(name):
        db_path = tmp_path / name
        db.register(db_path, 'https://alink', 1)
        return db_path

    return register_db


def test_write_read(mock_db):
    db_path = mock_db('prova.json')

    db.write(db_path, range(1, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_write_read_gzip(mock_db):
    db_path = mock_db('prova.json.gz')

    db.write(db_path, range(1, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_write_read_zstd(mock_db):
    db_path = mock_db('prova.json.zstd')

    db.write(db_path, range(1, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_append(mock_db):
    db_path = mock_db('prova.json')

    db.write(db_path, range(1, 4))

    assert list(db.read(db_path)) == [1, 2, 3]

    db.append(db_path, range(4, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_append_gzip(mock_db):
    db_path = mock_db('prova.json.gz')

    db.register(db_path, 'https://alink', 1)

    db.write(db_path, range(1, 4))

    assert list(db.read(db_path)) == [1, 2, 3]

    db.append(db_path, range(4, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_append_zstd(mock_db):
    db_path = mock_db('prova.json.zstd')

    db.write(db_path, range(1, 4))

    assert list(db.read(db_path)) == [1, 2, 3]

    db.append(db_path, range(4, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


def test_delete(mock_db):
    db_path = mock_db('prova.json')

    db.write(db_path, range(1, 9))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7, 8]

    db.delete(db_path, lambda x: x == 4)

    assert list(db.read(db_path)) == [1, 2, 3, 5, 6, 7, 8]


def test_delete_gzip(mock_db):
    db_path = mock_db('prova.json.gz')

    db.write(db_path, range(1, 9))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7, 8]

    db.delete(db_path, lambda x: x == 4)

    assert list(db.read(db_path)) == [1, 2, 3, 5, 6, 7, 8]


def test_delete_zstd(mock_db):
    db_path = mock_db('prova.json.zstd')

    db.register(db_path, 'https://alink', 1)

    db.write(db_path, range(1, 9))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7, 8]

    db.delete(db_path, lambda x: x == 4)

    assert list(db.read(db_path)) == [1, 2, 3, 5, 6, 7, 8]
