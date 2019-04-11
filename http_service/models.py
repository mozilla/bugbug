# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import lzma
import os
import shutil
import sys
from urllib.request import urlretrieve

import requests

from bugbug.models.component import ComponentModel
from bugbug.models.defect_enhancement_task import DefectEnhancementTaskModel
from bugbug.models.regression import RegressionModel

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()


MODELS = {
    "defectenhancementtask": DefectEnhancementTaskModel,
    "component": ComponentModel,
    "regression": RegressionModel,
}

BASE_URL = "https://index.taskcluster.net/v1/task/project.releng.services.project.testing.bugbug_train.latest/artifacts/public"


def retrieve_model(name):
    os.makedirs("models", exist_ok=True)

    file_name = f"{name}model"
    file_path = os.path.join("models", file_name)

    model_url = f"{BASE_URL}/{file_name}.xz"
    LOGGER.info(f"Checking ETAG of {model_url}")
    r = requests.head(model_url, allow_redirects=True)
    new_etag = r.headers["ETag"]

    try:
        with open(f"{file_path}.etag", "r") as f:
            old_etag = f.read()
    except IOError:
        old_etag = None

    if old_etag != new_etag:
        LOGGER.info(f"Downloading the model from {model_url}")
        urlretrieve(model_url, f"{file_path}.xz")

        with lzma.open(f"{file_path}.xz", "rb") as input_f:
            with open(file_path, "wb") as output_f:
                shutil.copyfileobj(input_f, output_f)

        with open(f"{file_path}.etag", "w") as f:
            f.write(new_etag)
    else:
        LOGGER.info(f"ETAG for {model_url} is ok")

    return file_path


def get_model_path(name):
    file_name = f"{name}model"
    file_path = os.path.join("models", file_name)

    return file_path


def preload_models():
    for model_name in MODELS.keys():
        retrieve_model(model_name)


def load_model(model):
    model_file_path = get_model_path(model)
    model = MODELS[model].load(model_file_path)
    return model


def check_models():
    for model_name in MODELS.keys():
        # Try loading the model
        load_model(model_name)


if __name__ == "__main__":
    if sys.argv[1] == "download":
        preload_models()
    elif sys.argv[1] == "check":
        try:
            check_models()
        except Exception:
            LOGGER.warning(
                "Failed to validate the models, please run `python models.py download`",
                exc_info=True,
            )
            sys.exit(1)
    else:
        raise ValueError(f"Unknown command {sys.argv[1]}")
