# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import pickle
from datetime import datetime
from urllib.parse import urljoin

import pytest
import requests
import responses
import zstandard

from bugbug import db


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
def test_write_read(mock_db, db_format, db_compression):
    db_path = mock_db(db_format, db_compression)

    db.write(db_path, range(1, 8))

    assert list(db.read(db_path)) == [1, 2, 3, 4, 5, 6, 7]


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


@pytest.fixture
def mock_zst():
    def create_zst_file(db_path):
        with open(db_path, "wb") as output_f:
            cctx = zstandard.ZstdCompressor()
            with cctx.stream_writer(output_f) as compressor:
                compressor.write(b'{"Hello": "World"}')

    return create_zst_file


def test_extract_db_zst(tmp_path, mock_zst):
    db_path = tmp_path / f"prova.zst"

    mock_zst(db_path)

    db.extract_file(db_path)

    with open(f"{os.path.splitext(db_path)[0]}", "rb") as f:
        file_decomp = json.load(f)

    assert file_decomp == {"Hello": "World"}


def test_extract_db_bad_format(tmp_path):
    db_path = tmp_path / "prova.pickle"

    with open(db_path, "wb") as output_f:
        pickle.dump({"Hello": "World"}, output_f)

    with pytest.raises(AssertionError):
        db.extract_file(db_path)


@responses.activate
def test_download_zst(tmp_path, mock_zst):
    url = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.zst"

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 1)

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

    db.download(db_path)

    assert db.last_modified(db_path) == datetime(2019, 4, 16)

    assert os.path.exists(db_path)
    assert os.path.exists(db_path.with_suffix(db_path.suffix + ".zst"))
    assert os.path.exists(db_path.with_suffix(db_path.suffix + ".zst.etag"))


@responses.activate
def test_download_missing(tmp_path):
    url = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.zst"

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

    db.download(db_path)
    assert not os.path.exists(db_path)

    with pytest.raises(Exception, match="Last-Modified is not available"):
        db.last_modified(db_path)


@responses.activate
def test_download_support_file_zst(tmp_path, mock_zst):
    url = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.zst"
    support_filename = "support.zst"
    url_support = urljoin(url, support_filename)

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 1, support_files=[support_filename])

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

    db.download_support_file(db_path, support_filename)

    assert os.path.exists(os.path.join(os.path.dirname(db_path), support_filename))
    assert os.path.exists(
        os.path.join(os.path.dirname(db_path), os.path.splitext(support_filename)[0])
    )
    assert os.path.exists(
        os.path.join(
            os.path.dirname(db_path),
            os.path.splitext(support_filename)[0] + ".zst.etag",
        )
    )


@responses.activate
def test_is_old_version(tmp_path):
    url_zst = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/prova.json.zst"
    url_version = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/prova.json.version"

    db_path = tmp_path / "prova.json"
    db.register(db_path, url_zst, 1, support_files=[])

    assert os.path.exists(db_path.with_suffix(db_path.suffix + ".version"))

    responses.add(responses.GET, url_version, status=404)
    responses.add(responses.GET, url_version, status=424)
    responses.add(responses.GET, url_version, status=200, body="1")
    responses.add(responses.GET, url_version, status=200, body="42")

    # When the remote version file doesn't exist, we consider the db as being old.
    assert db.is_old_version(db_path)

    # When the remote version file doesn't exist, we consider the db as being old.
    assert db.is_old_version(db_path)

    # When the remote version file exists and returns the same version as the current db, we consider the remote db as not being old.
    assert not db.is_old_version(db_path)

    # When the remote version file exists and returns a newer version than the current db, we consider the remote db as not being old.
    assert not db.is_old_version(db_path)

    db.register(db_path, url_zst, 43, support_files=[])

    # When the remote version file exists and returns an older version than the current db, we consider the remote db as being old.
    assert db.is_old_version(db_path)


@responses.activate
def test_download_support_file_missing(tmp_path, capfd):
    url = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.zst"
    support_filename = "support_mock.zst"
    url_support = urljoin(url, support_filename)

    db_path = tmp_path / "prova.json"
    db.register(db_path, url, 1, support_files=[support_filename])

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

    db.download_support_file(db_path, support_filename)

    out, err = capfd.readouterr()
    path = os.path.join(
        os.path.dirname(db_path), f"{os.path.splitext(support_filename)[0]}.zst"
    )
    assert (
        out.split("\n")[-2]
        == f"{support_filename} is not yet available to download for {path}"
    )
