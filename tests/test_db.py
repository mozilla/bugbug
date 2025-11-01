# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
from datetime import datetime
from urllib.parse import urljoin

import pytest
import requests
import responses

from bugbug import db
from bugbug.db import LastModifiedNotAvailable


@pytest.fixture
def mock_db(tmp_path):
    def register_db(db_format, db_compression):
        db_name = f"prova.{db_format}"
        if db_compression is not None:
            db_name += f".{db_compression}"

        db_path = tmp_path / db_name
        db.register(db_path, "https://alink", 1)
        return db_path

    return register_db


@pytest.mark.parametrize("db_format", ["json", "pickle"])
@pytest.mark.parametrize("db_compression", [None, "gz", "zstd"])
def test_write_read_size(mock_db, db_format, db_compression):
    db_path = mock_db(db_format, db_compression)

    db.write(db_path, range(1, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]
    assert db.size(db_path) == 7


@pytest.mark.parametrize("db_format", ["json", "pickle"])
@pytest.mark.parametrize("db_compression", [None, "gz", "zstd"])
def test_read_empty(mock_db, db_format, db_compression):
    db_path = mock_db(db_format, db_compression)

    assert list(db.read(db_path)) == []
    assert db.size(db_path) == 0

    db.write(db_path, [])

    assert list(db.read(db_path)) == []
    assert db.size(db_path) == 0


@pytest.mark.parametrize("db_format", ["json", "pickle"])
@pytest.mark.parametrize("db_compression", [None, "gz", "zstd"])
def test_append(mock_db, db_format, db_compression):
    db_path = mock_db(db_format, db_compression)

    db.write(db_path, range(1, 4))

    assert list(db.read(db_path)) == [1, 2, 3]

    db.append(db_path, range(4, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


@pytest.mark.parametrize("db_format", ["json", "pickle"])
@pytest.mark.parametrize("db_compression", [None, "gz", "zstd"])
def test_delete(mock_db, db_format, db_compression):
    db_path = mock_db(db_format, db_compression)

    db.write(db_path, range(1, 9))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7, 8]

    db.delete(db_path, lambda x: x == 4)

    assert list(db.read(db_path)) == [1, 2, 3, 5, 6, 7, 8]


def test_delete_not_existent(mock_db):
    db_path = mock_db("json", None)
    assert not os.path.exists(db_path)
    db.delete(db_path, lambda x: x == 4)
    assert not os.path.exists(db_path)


def test_unregistered_db(tmp_path):
    db_path = tmp_path / "prova.json"

    with pytest.raises(AssertionError):
        list(db.read(db_path))

    with pytest.raises(AssertionError):
        db.write(db_path, range(7))

    with pytest.raises(AssertionError):
        db.append(db_path, range(7))


@pytest.mark.parametrize(
    "db_name", ["prova", "prova.", "prova.gz", "prova.unknown.gz", "prova.json.unknown"]
)
def test_bad_format_compression(tmp_path, db_name):
    db_path = tmp_path / db_name
    db.register(db_path, "https://alink", 1)

    with pytest.raises(AssertionError):
        db.write(db_path, range(7))

    with pytest.raises(AssertionError):
        db.append(db_path, range(7))


def test_register_db(tmp_path):
    db_path = tmp_path / "prova.json"

    db.register(db_path, "https://alink", 1)

    assert os.path.exists(db_path.with_suffix(db_path.suffix + ".version"))


def test_exists_db(tmp_path):
    db_path = tmp_path / "prova.json"

    db.register(db_path, "https://alink", 1)

    assert not db.exists(db_path)

    db.write(db_path, range(7))

    assert db.exists(db_path)


def test_download(tmp_path, mock_zst):
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.zst"
    url_version = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.version"

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 1)

    responses.add(responses.GET, url_version, status=200, body="1")

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "ETag": "123",
            "Accept-Encoding": "zstd",
            "Last-Modified": "2019-04-16",
        },
    )

    tmp_zst_path = tmp_path / "prova_tmp.zst"
    mock_zst(tmp_zst_path)

    with open(tmp_zst_path, "rb") as content:
        responses.add(responses.GET, url, status=200, body=content.read())

    assert db.download(db_path)
    assert db.download(db_path)

    assert db.last_modified(db_path) == datetime(2019, 4, 16)

    assert os.path.exists(db_path)
    assert not os.path.exists(db_path.with_suffix(db_path.suffix + ".zst"))
    assert os.path.exists(db_path.with_suffix(db_path.suffix + ".zst.etag"))


def test_download_missing(tmp_path, mock_zst):
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.zst"
    url_version = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.version"

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 1)

    responses.add(
        responses.HEAD,
        url,
        status=404,
        headers={"ETag": "123", "Accept-Encoding": "zstd"},
    )

    responses.add(
        responses.GET, url, status=404, body=requests.exceptions.HTTPError("HTTP error")
    )

    responses.add(responses.GET, url_version, status=404)

    assert not db.download(db_path)
    assert not os.path.exists(db_path)

    with pytest.raises(LastModifiedNotAvailable):
        db.last_modified(db_path)


def test_download_different_schema(tmp_path, mock_zst):
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.zst"
    url_version = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.version"

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 2)

    responses.add(responses.GET, url_version, status=200, body="1")

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "ETag": "123",
            "Accept-Encoding": "zstd",
            "Last-Modified": "2019-04-16",
        },
    )

    tmp_zst_path = tmp_path / "prova_tmp.zst"
    mock_zst(tmp_zst_path)

    with open(tmp_zst_path, "rb") as content:
        responses.add(responses.GET, url, status=200, body=content.read())

    assert not db.download(db_path)

    with pytest.raises(db.LastModifiedNotAvailable):
        db.last_modified(db_path)

    assert not os.path.exists(db_path)
    assert not os.path.exists(db_path.with_suffix(db_path.suffix + ".zst"))
    assert not os.path.exists(db_path.with_suffix(db_path.suffix + ".zst.etag"))


def test_download_same_schema_new_db(tmp_path, mock_zst):
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.zst"
    url_version = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.version"

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 1)

    responses.add(responses.GET, url_version, status=200, body="1")

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "ETag": "123",
            "Accept-Encoding": "zstd",
        },
    )

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "ETag": "456",
            "Accept-Encoding": "zstd",
        },
    )

    tmp_zst_path1 = tmp_path / "prova_tmp.zst"
    mock_zst(tmp_zst_path1, b"0")

    with open(tmp_zst_path1, "rb") as content:
        responses.add(responses.GET, url, status=200, body=content.read())

    tmp_zst_path2 = tmp_path / "prova_tmp2.zst"
    mock_zst(tmp_zst_path2, b"1")

    with open(tmp_zst_path2, "rb") as content:
        responses.add(responses.GET, url, status=200, body=content.read())

    assert db.download(db_path)

    assert os.path.exists(db_path)
    assert not os.path.exists(db_path.with_suffix(db_path.suffix + ".zst"))
    assert os.path.exists(db_path.with_suffix(db_path.suffix + ".zst.etag"))

    with open(db_path, "r") as f:
        assert f.read() == "0"

    assert db.download(db_path)

    assert os.path.exists(db_path)
    assert not os.path.exists(db_path.with_suffix(db_path.suffix + ".zst"))
    assert os.path.exists(db_path.with_suffix(db_path.suffix + ".zst.etag"))

    with open(db_path, "r") as f:
        assert f.read() == "1"


def test_download_support_file(tmp_path, mock_zst):
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.zst"
    url_version = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.version"
    support_filename = "support.zst"
    url_support = urljoin(url, support_filename)

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 1, support_files=[support_filename])

    responses.add(responses.GET, url_version, status=200, body="1")

    responses.add(
        responses.HEAD,
        url_support,
        status=200,
        headers={"ETag": "123", "Accept-Encoding": "zstd"},
    )

    tmp_zst_path = tmp_path / "prova_tmp.zst"
    mock_zst(tmp_zst_path)

    with open(tmp_zst_path, "rb") as content:
        responses.add(responses.GET, url_support, status=200, body=content.read())

    assert db.download_support_file(db_path, support_filename)

    assert not os.path.exists(os.path.join(os.path.dirname(db_path), support_filename))
    assert os.path.exists(
        os.path.join(os.path.dirname(db_path), os.path.splitext(support_filename)[0])
    )
    assert os.path.exists(
        os.path.join(
            os.path.dirname(db_path),
            os.path.splitext(support_filename)[0] + ".zst.etag",
        )
    )


def test_download_with_support_files_too(tmp_path, mock_zst):
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.zst"
    url_version = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.version"
    support_filename = "support.zst"
    url_support = urljoin(url, support_filename)

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 1, support_files=[support_filename])

    responses.add(responses.GET, url_version, status=200, body="1")

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={"ETag": "123", "Accept-Encoding": "zstd"},
    )

    responses.add(
        responses.HEAD,
        url_support,
        status=200,
        headers={"ETag": "123", "Accept-Encoding": "zstd"},
    )

    tmp_zst_path = tmp_path / "prova_tmp.zst"
    mock_zst(tmp_zst_path)

    with open(tmp_zst_path, "rb") as content:
        responses.add(responses.GET, url, status=200, body=content.read())

    with open(tmp_zst_path, "rb") as content:
        responses.add(responses.GET, url_support, status=200, body=content.read())

    assert db.download(db_path, support_files_too=True)

    assert os.path.exists(db_path)
    assert not os.path.exists(db_path.with_suffix(db_path.suffix + ".zst"))
    assert os.path.exists(db_path.with_suffix(db_path.suffix + ".zst.etag"))
    assert not os.path.exists(os.path.join(os.path.dirname(db_path), support_filename))
    assert os.path.exists(
        os.path.join(os.path.dirname(db_path), os.path.splitext(support_filename)[0])
    )
    assert os.path.exists(
        os.path.join(
            os.path.dirname(db_path),
            os.path.splitext(support_filename)[0] + ".zst.etag",
        )
    )


def test_download_support_file_missing(tmp_path, caplog):
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/commits.json.zst"
    url_version = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.version"
    support_filename = "support_mock.zst"
    url_support = urljoin(url, support_filename)

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 1, support_files=[support_filename])

    responses.add(responses.GET, url_version, status=404)

    responses.add(
        responses.HEAD,
        url_support,
        status=404,
        headers={"ETag": "123", "Accept-Encoding": "zstd"},
    )

    responses.add(
        responses.GET,
        url_support,
        status=404,
        body=requests.exceptions.HTTPError("HTTP error"),
    )

    assert not db.download_support_file(db_path, support_filename)

    expected_message = f"Version file is not yet available to download for {db_path}"
    assert expected_message in caplog.text


def test_is_different_schema(tmp_path):
    url_zst = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.zst"
    url_version = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_commits.latest/artifacts/public/prova.json.version"

    db_path = tmp_path / "prova.json"
    db.register(db_path, url_zst, 1, support_files=[])

    assert os.path.exists(db_path.with_suffix(db_path.suffix + ".version"))

    responses.add(responses.GET, url_version, status=404)
    responses.add(responses.GET, url_version, status=424)
    responses.add(responses.GET, url_version, status=200, body="1")
    responses.add(responses.GET, url_version, status=200, body="42")

    # When the remote version file doesn't exist (due to 404 status), we consider the current db version as being different.
    assert db.is_different_schema(db_path)

    # When the remote version file doesn't exist (due to 424 status), we consider the current db version as being different.
    assert db.is_different_schema(db_path)

    # When the remote version file exists and returns the same version as the current db, we consider that the current db version is not different from remote db version.
    assert not db.is_different_schema(db_path)

    # When the remote version file exists and returns a newer version than the current db, we consider that the current db version is different from remote db version.
    assert db.is_different_schema(db_path)

    db.register(db_path, url_zst, 43, support_files=[])

    # When the remote version file exists and returns an older version than the current db, we consider that the current db version is different from remote db version.
    assert db.is_different_schema(db_path)
