# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import os
import pickle
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
import requests
import responses
import urllib3
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import CountVectorizer

from bugbug import utils


def test_split_tuple_iterator():
    X, y = utils.split_tuple_generator(
        lambda: [("val1", "label1"), ("val2", "label2"), ("val3", "label3")]
    )

    assert list(X()) == ["val1", "val2", "val3"]
    assert list(y) == ["label1", "label2", "label3"]
    assert list(y) == ["label1", "label2", "label3"]
    assert list(X()) == ["val1", "val2", "val3"]
    assert list(y) == ["label1", "label2", "label3"]


def test_exp_queue():
    q = utils.ExpQueue(0, 4, 0)
    q[0] = 1
    assert q[0] == 1
    q[0] = 2
    assert q[0] == 2

    q = utils.ExpQueue(366, 91, 0)
    assert q[366] == 0
    assert q[276] == 0
    q[366] += 1
    assert q[367] == 1
    assert q[277] == 0

    q = utils.ExpQueue(0, 4, 0)
    assert q[0] == 0
    q[0] += 1
    assert q[0] == 1
    q[0] += 1
    assert q[0] == 2
    assert q[1] == 2
    q[1] += 1
    assert q[1] == 3
    assert q[9] == 3
    q[9] += 1
    assert q[9] == 4
    assert q[6] == 3
    assert q[11] == 4
    q[11] += 1
    assert q[11] == 5
    q[12] += 1
    assert q[12] == 6
    q[13] += 1
    assert q[13] == 7
    q[14] += 1
    assert q[14] == 8
    q[15] += 1
    assert q[15] == 9

    q = utils.ExpQueue(0, 4, 0)
    assert q[0] == 0
    q[0] += 1
    assert q[0] == 1
    assert q[1] == 1
    assert q[9] == 1
    q[9] += 1
    assert q[9] == 2
    assert q[10] == 2
    assert q[8] == 1
    assert q[7] == 1
    assert q[6] == 1

    q = utils.ExpQueue(9, 3, 0)
    assert q[8] == 0
    assert q[9] == 0
    q[9] += 1
    assert q[11] == 1
    assert q[10] == 1
    assert q[8] == 0
    assert q[11] == 1
    assert q[8] == 0
    assert q[9] == 1
    assert q[12] == 1


def test_download_check_etag():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "ETag": "123",
            "Last-Modified": "2019-04-16",
        },
    )

    responses.add(responses.GET, url, status=200, body="prova")

    assert utils.download_check_etag(url)

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova"

    assert utils.download_check_etag(url, "data/prova2.txt")

    assert os.path.exists("data/prova2.txt")

    assert not os.path.exists("prova2.txt")

    with open("data/prova2.txt", "r") as f:
        assert f.read() == "prova"


def test_download_check_etag_changed():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "ETag": "123",
            "Last-Modified": "2019-04-16",
        },
    )

    responses.add(responses.GET, url, status=200, body="prova")

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "ETag": "456",
            "Last-Modified": "2019-04-16",
        },
    )

    responses.add(responses.GET, url, status=200, body="prova2")

    assert utils.download_check_etag(url)

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova"

    assert utils.download_check_etag(url)

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova2"


def test_download_check_etag_unchanged():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "ETag": "123",
            "Last-Modified": "2019-04-16",
        },
    )

    responses.add(responses.GET, url, status=200, body="prova")

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "ETag": "123",
            "Last-Modified": "2019-04-16",
        },
    )

    responses.add(responses.GET, url, status=200, body="prova2")

    assert utils.download_check_etag(url)

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova"

    assert not utils.download_check_etag(url)

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova"


def test_download_check_missing():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=404,
        headers={
            "ETag": "123",
            "Last-Modified": "2019-04-16",
        },
    )

    responses.add(
        responses.GET, url, status=404, body=requests.exceptions.HTTPError("HTTP error")
    )

    with pytest.raises(requests.exceptions.HTTPError, match="404 Client Error"):
        utils.download_check_etag(url)

    assert not os.path.exists("prova.txt")


def test_download_check_missing_etag():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=404,
        headers={
            "Last-Modified": "2019-04-16",
        },
    )

    responses.add(
        responses.GET, url, status=404, body=requests.exceptions.HTTPError("HTTP error")
    )

    with pytest.raises(requests.exceptions.HTTPError, match="404 Client Error"):
        utils.download_check_etag(url)

    assert not os.path.exists("prova.txt")


def test_get_last_modified():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={
            "Last-Modified": "2019-04-16",
        },
    )

    assert utils.get_last_modified(url) == datetime(2019, 4, 16, 0, 0)


def test_get_last_modified_not_present():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={"ETag": "123"},
    )

    assert utils.get_last_modified(url) is None


def test_get_last_modified_missing():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=404,
        headers={},
    )

    assert utils.get_last_modified(url) is None


def test_get_last_modified_error():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=429,
        headers={},
    )

    with pytest.raises(urllib3.exceptions.MaxRetryError, match="Max retries exceeded"):
        utils.get_last_modified(url)

    assert not os.path.exists("prova.txt")


def test_zstd_compress_decompress(tmp_path):
    path = tmp_path / "prova"
    compressed_path = path.with_suffix(".zst")

    with open(path, "w") as f:
        json.dump({"Hello": "World"}, f)

    utils.zstd_compress(path)

    assert os.path.exists(compressed_path)
    os.remove(path)

    utils.zstd_decompress(path)

    with open(path, "r") as f:
        file_decomp = json.load(f)

    assert file_decomp == {"Hello": "World"}


def test_zstd_compress_not_existing(tmp_path, mock_zst):
    path = tmp_path / "prova"
    compressed_path = path.with_suffix(".zst")

    with pytest.raises(FileNotFoundError):
        utils.zstd_compress(path)

    assert not os.path.exists(compressed_path)


def test_create_extract_tar_zst(tmp_path):
    path = tmp_path / "prova"
    tar_zst_path = "prova.tar.zst"

    with open(path, "w") as f:
        json.dump({"Hello": "World"}, f)

    utils.create_tar_zst(tar_zst_path)

    assert os.path.exists(tar_zst_path)

    os.remove(path)

    utils.extract_tar_zst(tar_zst_path)

    assert os.path.exists(path)

    with open(path, "r") as f:
        file_decomp = json.load(f)

    assert file_decomp == {"Hello": "World"}


def test_extract_db_zst(tmp_path, mock_zst):
    path = tmp_path / "prova.zst"

    mock_zst(path)

    utils.extract_file(path)

    with open(f"{os.path.splitext(path)[0]}", "rb") as f:
        file_decomp = json.load(f)

    assert file_decomp == {"Hello": "World"}


def test_extract_db_bad_format(tmp_path):
    path = tmp_path / "prova.pickle"

    with open(path, "wb") as output_f:
        pickle.dump({"Hello": "World"}, output_f)

    with pytest.raises(AssertionError):
        utils.extract_file(path)


def test_extract_metadata() -> None:
    body = """
        <!-- @private_url: https://github.com/webcompat/web-bugs-private/issues/12345 -->\n
        """

    expected = {
        "private_url": "https://github.com/webcompat/web-bugs-private/issues/12345"
    }
    result = utils.extract_metadata(body)
    assert result == expected

    result = utils.extract_metadata("test")
    assert result == {}


def test_extract_private_url() -> None:
    body = """
    <p>Thanks for the report. We have closed this issue\n
    automatically as we suspect it is invalid. If we made
    a mistake, please\nfile a new issue and try to provide
    more context.</p>\n
    <!-- @private_url: https://github.com/webcompat/web-bugs-private/issues/12345 -->\n
    """
    expected = ("webcompat", "web-bugs-private", "12345")
    result = utils.extract_private(body)
    assert result == expected


def test_extract_private_url_empty() -> None:
    body = """<p>Test content</p> """
    result = utils.extract_private(body)
    assert result is None


def test_StructuredColumnTransformer() -> None:
    transformers = [
        ("feat1_transformed", CountVectorizer(), "feat1"),
        ("feat2_transformed", CountVectorizer(), "feat2"),
    ]

    df = pd.DataFrame(
        [
            {
                "feat1": "First",
                "feat2": "Second",
            },
            {
                "feat1": "Third",
                "feat2": "Fourth",
            },
        ]
    )

    np.testing.assert_array_equal(
        ColumnTransformer(transformers).fit_transform(df),
        np.array([[1, 0, 0, 1], [0, 1, 1, 0]]),
    )

    np.testing.assert_array_equal(
        utils.StructuredColumnTransformer(transformers).fit_transform(df),
        np.array(
            [[([1, 0], [0, 1])], [([0, 1], [1, 0])]],
            dtype=[
                ("feat1_transformed", "<i8", (2,)),
                ("feat2_transformed", "<i8", (2,)),
            ],
        ),
    )

    np.testing.assert_array_equal(
        utils.StructuredColumnTransformer(transformers)
        .fit_transform(df)
        .view(np.dtype("int64")),
        ColumnTransformer(transformers).fit_transform(df),
    )
