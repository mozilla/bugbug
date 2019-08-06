# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json

import pytest

from http_service.app import (  # TODO: Move http_service under bugbug to solve this import name
    API_TOKEN,
    application,
)


@pytest.fixture
def client():
    yield application.test_client()


def test_empty_batch(client):
    """Start with a blank database."""

    rv = client.post(
        "/component/predict/batch",
        data=json.dumps({"bugs": []}),
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 400
    assert rv.json == {"errors": {"bugs": ["min length is 1"]}}


def test_too_big_batch(client):
    """Start with a blank database."""

    bugs = list(range(1001))

    rv = client.post(
        "/component/predict/batch",
        data=json.dumps({"bugs": bugs}),
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 400
    assert rv.json == {"errors": {"bugs": ["max length is 1000"]}}


def test_non_int_batch(client):
    """Start with a blank database."""

    bugs = ["1", "2", "3"]

    rv = client.post(
        "/component/predict/batch",
        data=json.dumps({"bugs": bugs}),
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 400
    assert rv.json == {
        "errors": {
            "bugs": [
                {
                    "0": ["must be of integer type"],
                    "1": ["must be of integer type"],
                    "2": ["must be of integer type"],
                }
            ]
        }
    }
