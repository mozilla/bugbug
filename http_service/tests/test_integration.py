# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import time

import requests

BUGBUG_HTTP_SERVER = os.environ.get("BUGBUG_HTTP_SERVER", "http://localhost:8000/")


# Test classifying a single bug.
def integration_test_single():
    timeout = 1200
    for _ in range(timeout):
        response = requests.get(
            f"{BUGBUG_HTTP_SERVER}/defectenhancementtask/predict/1376406",
            headers={"X-Api-Key": "integration_test_single"},
        )

        if response.status_code == 200:
            break

        time.sleep(1)

    response_json = response.json()

    if not response.ok:
        raise Exception(f"Couldn't get an answer in {timeout} seconds: {response_json}")

    print("Response for bug 1376406", response_json)
    assert response_json["class"] is not None


# Test classifying a batch of bugs.
def integration_test_batch():
    timeout = 100
    for _ in range(timeout):
        response = requests.post(
            f"{BUGBUG_HTTP_SERVER}/defectenhancementtask/predict/batch",
            headers={"X-Api-Key": "integration_test_batch"},
            json={"bugs": [1376544, 1376412]},
        )

        if response.status_code == 200:
            break

        time.sleep(1)

    response_json = response.json()

    if not response.ok:
        raise Exception(f"Couldn't get an answer in {timeout} seconds: {response_json}")

    response_1376544 = response_json["bugs"]["1376544"]
    print("Response for bug 1376544", response_1376544)
    assert response_1376544["class"] is not None
    response_1376412 = response_json["bugs"]["1376412"]
    print("Response for bug 1376412", response_1376412)
    assert response_1376412["class"] is not None


if __name__ == "__main__":
    integration_test_single()
    integration_test_batch()
