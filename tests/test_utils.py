# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
from datetime import datetime

import pytest
import requests
import responses

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
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={"ETag": "123", "Last-Modified": "2019-04-16",},
    )

    responses.add(responses.GET, url, status=200, body="prova")

    assert utils.download_check_etag(url, "prova.txt")

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova"


def test_download_check_etag_changed():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={"ETag": "123", "Last-Modified": "2019-04-16",},
    )

    responses.add(responses.GET, url, status=200, body="prova")

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={"ETag": "456", "Last-Modified": "2019-04-16",},
    )

    responses.add(responses.GET, url, status=200, body="prova2")

    assert utils.download_check_etag(url, "prova.txt")

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova"

    assert utils.download_check_etag(url, "prova.txt")

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova2"


def test_download_check_etag_unchanged():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={"ETag": "123", "Last-Modified": "2019-04-16",},
    )

    responses.add(responses.GET, url, status=200, body="prova")

    responses.add(
        responses.HEAD,
        url,
        status=200,
        headers={"ETag": "123", "Last-Modified": "2019-04-16",},
    )

    responses.add(responses.GET, url, status=200, body="prova2")

    assert utils.download_check_etag(url, "prova.txt")

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova"

    assert not utils.download_check_etag(url, "prova.txt")

    assert os.path.exists("prova.txt")

    with open("prova.txt", "r") as f:
        assert f.read() == "prova"


def test_download_check_missing():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug/prova.txt"

    responses.add(
        responses.HEAD,
        url,
        status=404,
        headers={"ETag": "123", "Last-Modified": "2019-04-16",},
    )

    responses.add(
        responses.GET, url, status=404, body=requests.exceptions.HTTPError("HTTP error")
    )

    with pytest.raises(requests.exceptions.HTTPError, match="HTTP error"):
        utils.download_check_etag(url, "prova.txt")

    assert not os.path.exists("prova.txt")


def test_get_last_modified():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug/prova.txt"

    responses.add(
        responses.HEAD, url, status=200, headers={"Last-Modified": "2019-04-16",},
    )

    assert utils.get_last_modified(url) == datetime(2019, 4, 16, 0, 0)


def test_get_last_modified_not_present():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug/prova.txt"

    responses.add(
        responses.HEAD, url, status=200, headers={"ETag": "123"},
    )

    assert utils.get_last_modified(url) is None


def test_get_last_modified_missing():
    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug/prova.txt"

    responses.add(
        responses.HEAD, url, status=404, headers={},
    )

    assert utils.get_last_modified(url) is None
