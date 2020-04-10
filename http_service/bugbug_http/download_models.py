# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

from bugbug import utils
from bugbug_http import ALLOW_MISSING_MODELS
from bugbug_http.models import MODEL_CACHE, MODELS_NAMES

LOGGER = logging.getLogger()


def download_models():
    for model_name in MODELS_NAMES:
        utils.download_model(model_name)
        # Try loading the model
        try:
            m = MODEL_CACHE.get(model_name)
            m.download_eval_dbs(extract=False, ensure_exist=not ALLOW_MISSING_MODELS)
        except FileNotFoundError:
            if ALLOW_MISSING_MODELS:
                LOGGER.info(
                    "Missing %r model, skipping because ALLOW_MISSING_MODELS is set"
                    % model_name
                )
                return None
            else:
                raise


if __name__ == "__main__":
    download_models()
