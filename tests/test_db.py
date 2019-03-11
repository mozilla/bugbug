# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import pytest

from bugbug import db


@pytest.fixture
def mock_db(tmp_path):
    def register_db(db_format, db_compression):
        db_name = f'prova.{db_format}'
        if db_compression is not None:
            db_name += f'.{db_compression}'

        db_path = tmp_path / db_name
        db.register(db_path, 'https://alink', 1)
        return db_path

    return register_db


@pytest.mark.parametrize('db_format', ['json', 'pickle'])
@pytest.mark.parametrize('db_compression', [None, 'gz', 'zstd'])
def test_write_read(mock_db, db_format, db_compression):
    db_path = mock_db(db_format, db_compression)

    print(db_path)

    db.write(db_path, range(1, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize('db_format', ['json', 'pickle'])
@pytest.mark.parametrize('db_compression', [None, 'gz', 'zstd'])
def test_append(mock_db, db_format, db_compression):
    db_path = mock_db(db_format, db_compression)

    db.write(db_path, range(1, 4))

    assert list(db.read(db_path)) == [1, 2, 3]

    db.append(db_path, range(4, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize('db_format', ['json', 'pickle'])
@pytest.mark.parametrize('db_compression', [None, 'gz', 'zstd'])
def test_delete(mock_db, db_format, db_compression):
    db_path = mock_db(db_format, db_compression)

    db.write(db_path, range(1, 9))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7, 8]

    db.delete(db_path, lambda x: x == 4)

    assert list(db.read(db_path)) == [1, 2, 3, 5, 6, 7, 8]
