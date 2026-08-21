# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

from bugbug import utils
from bugbug.models import get_model_class
from bugbug_http import ALLOW_MISSING_MODELS
from bugbug_http.models import MODEL_CACHE, MODELS_TO_DOWNLOAD

LOGGER = logging.getLogger()


def download_models():
    for model_name in MODELS_TO_DOWNLOAD:
        # Some models are published outside the Taskcluster index.
        artifact_url = getattr(get_model_class(model_name), "artifact_url", None)
        if artifact_url:
            utils.download_model_from_url(model_name, artifact_url)
        else:
            utils.download_model(model_name)
        # Try loading the model
        try:
            m = MODEL_CACHE.get(model_name)
            m.download_eval_dbs(extract=False, ensure_exist=not ALLOW_MISSING_MODELS)
        except FileNotFoundError:
            if ALLOW_MISSING_MODELS:
                LOGGER.info(
                    "Missing %r model, skipping because ALLOW_MISSING_MODELS is set",
                    model_name,
                )
                return None
            else:
                raise


if __name__ == "__main__":
    download_models()
