# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import time

import requests

BUGBUG_HTTP_SERVER = os.environ.get("BUGBUG_HTTP_SERVER", "http://localhost:8000/")


def integration_test():
    # First try to classify a single bug
    single_bug_url = f"{BUGBUG_HTTP_SERVER}/defectenhancementtask/predict/1376406"
    response = None
    for i in range(100):
        response = requests.get(single_bug_url, headers={"X-Api-Key": "Test"})

        if response.status_code == 200:
            break

        time.sleep(1)

    if not response:
        raise Exception("Couldn't get an answer in 100 seconds")

    response_json = response.json()
    print("Response for bug 1376406", response_json)
    assert response_json["class"] is not None

    # Then try to classify a batch
    batch_url = f"{BUGBUG_HTTP_SERVER}/defectenhancementtask/predict/batch"
    bug_ids = [1_376_544, 1_376_412]
    response = None
    for i in range(100):
        response = requests.post(
            batch_url, headers={"X-Api-Key": "Test"}, json={"bugs": bug_ids}
        )

        if response.status_code == 200:
            break

        time.sleep(1)

    if not response:
        raise Exception("Couldn't get an answer in 100 seconds")

    response_json = response.json()
    response_1376544 = response_json["bugs"]["1376544"]
    print("Response for bug 1376544", response_1376544)
    assert response_1376544["class"] is not None
    response_1376412 = response_json["bugs"]["1376412"]
    print("Response for bug 1376412", response_1376412)
    assert response_1376412["class"] is not None


if __name__ == "__main__":
    integration_test()
