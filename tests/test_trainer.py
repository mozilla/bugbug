# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re

import responses

from bugbug import bugzilla, db
from scripts import trainer


# Test xgboost model on TF-IDF
def test_trainer_simple():
    # Pretend the DB was already downloaded and no new DB is available.

    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_bugs.latest/artifacts/public/bugs.json"

    responses.add(
        responses.GET,
        f"{url}.version",
        status=200,
        body=str(db.DATABASES[bugzilla.BUGS_DB]["version"]),
    )

    responses.add(
        responses.HEAD,
        f"{url}.zst",
        status=200,
        headers={"ETag": "etag"},
    )

    trainer.Trainer().go(trainer.parse_args(["regression"]))


# Test finetuning of transformer model
def test_trainer_finetuning():
    responses.add_passthru(re.compile(r"https://.*\.?huggingface.co/\w+"))

    # Pretend the DB was already downloaded and no new DB is available.

    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_bugs.latest/artifacts/public/bugs.json"

    responses.add(
        responses.GET,
        f"{url}.version",
        status=200,
        body=str(db.DATABASES[bugzilla.BUGS_DB]["version"]),
    )

    responses.add(
        responses.HEAD,
        f"{url}.zst",
        status=200,
        headers={"ETag": "etag"},
    )

    trainer.Trainer().go(trainer.parse_args(["defect_finetuning"]))


# Test xgboost model on transformed model's embeddings
def test_trainer_embedding():
    responses.add_passthru(re.compile(r"https://.*\.?huggingface.co/\w+"))

    # Pretend the DB was already downloaded and no new DB is available.

    url = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_bugs.latest/artifacts/public/bugs.json"

    responses.add(
        responses.GET,
        f"{url}.version",
        status=200,
        body=str(db.DATABASES[bugzilla.BUGS_DB]["version"]),
    )

    responses.add(
        responses.HEAD,
        f"{url}.zst",
        status=200,
        headers={"ETag": "etag"},
    )

    trainer.Trainer().go(trainer.parse_args(["defect_embedding"]))
