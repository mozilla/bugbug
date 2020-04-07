# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging

from bugbug import db, repository, test_scheduling, utils
from bugbug_http import ALLOW_MISSING_MODELS
from bugbug_http.models import MODEL_CACHE, MODELS_NAMES

LOGGER = logging.getLogger()


def download_models():
    for model_name in MODELS_NAMES:
        utils.download_model(model_name)
        # Try loading the model
        try:
            MODEL_CACHE.get(model_name)
        except FileNotFoundError:
            if ALLOW_MISSING_MODELS:
                LOGGER.info(
                    "Missing %r model, skipping because ALLOW_MISSING_MODELS is set"
                    % model_name
                )
                return None
            else:
                raise

    db.download_support_file(
        test_scheduling.TEST_LABEL_SCHEDULING_DB,
        test_scheduling.PAST_FAILURES_LABEL_DB,
        extract=False,
    )

    db.download_support_file(
        test_scheduling.TEST_GROUP_SCHEDULING_DB,
        test_scheduling.PAST_FAILURES_GROUP_DB,
        extract=False,
    )

    db.download_support_file(
        test_scheduling.TEST_GROUP_SCHEDULING_DB,
        test_scheduling.TOUCHED_TOGETHER_DB,
        extract=False,
    )

    db.download_support_file(
        repository.COMMITS_DB, repository.COMMIT_EXPERIENCES_DB, extract=False
    )

    db.download(repository.COMMITS_DB, extract=False)


if __name__ == "__main__":
    download_models()
