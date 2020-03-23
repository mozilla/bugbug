# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import db, repository, test_scheduling, utils
from bugbug_http.models import MODELS_NAMES


def preload_models():
    for model_name in MODELS_NAMES:
        utils.download_model(model_name)

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
    preload_models()
