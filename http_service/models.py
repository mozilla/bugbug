# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import lzma
import os
import shutil
from urllib.request import urlretrieve

import requests

from bugbug.models import load_model as bugbug_load_model

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()

MODELS_NAMES = ["defectenhancementtask", "component", "regression"]
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
BASE_URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.train_{}.latest/artifacts/public"


def load_model(model):
    # TODO: Do not crash when the asked model is not one of the trained models
    return bugbug_load_model(model, MODELS_DIR)


def retrieve_model(name):
    os.makedirs(MODELS_DIR, exist_ok=True)

    file_name = f"{name}model"
    file_path = os.path.join(MODELS_DIR, file_name)

    base_url = BASE_URL.format(name)
    model_url = f"{base_url}/{file_name}.xz"
    LOGGER.info(f"Checking ETAG of {model_url}")
    r = requests.head(model_url, allow_redirects=True)
    r.raise_for_status()
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
                LOGGER.info(f"Written model in {file_path}")

        with open(f"{file_path}.etag", "w") as f:
            f.write(new_etag)
    else:
        LOGGER.info(f"ETAG for {model_url} is ok")

    return file_path
