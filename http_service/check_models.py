# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import sys

# Non-relative imports might be brittle
from models import MODELS_NAMES, load_model

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()


def check_models():
    for model_name in MODELS_NAMES:
        # Try loading the model
        load_model(model_name)


if __name__ == "__main__":
    try:
        check_models()
    except Exception:
        LOGGER.warning(
            "Failed to validate the models, please run `python download_models.py`",
            exc_info=True,
        )
        sys.exit(1)
