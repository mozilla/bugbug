# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
import sys

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
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")


def load_model(model):
    model_file_path = os.path.join(MODELS_DIR, f"{model}model")
    LOGGER.info(f"Lookup model in {model_file_path}")
    model = MODELS[model].load(model_file_path)
    return model


def check_models():
    for model_name in MODELS.keys():
        # Try loading the model
        load_model(model_name)


if __name__ == "__main__":

    should_check_models = os.environ.get("CHECK_MODELS", "1")

    if should_check_models == "0":
        print("Skipping checking models as instructed by env var $CHECK_MODELS")
        sys.exit(0)

    try:
        check_models()
    except Exception:
        LOGGER.warning(
            "Failed to validate the models, please run `python models.py download`",
            exc_info=True,
        )
        sys.exit(1)
