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
    assert response_json["class"] == "defect", response_json

    # Then try to classify a batch
    batch_url = f"{BUGBUG_HTTP_SERVER}/defectenhancementtask/predict/batch"
    bug_ids = [1_376_535, 1_376_412]
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
    assert response_json["bugs"]["1376535"]["class"] == "enhancement", response_json[
        "bugs"
    ]["1376535"]
    assert response_json["bugs"]["1376412"]["class"] == "task", response_json["bugs"][
        "1376412"
    ]


if __name__ == "__main__":
    integration_test()
