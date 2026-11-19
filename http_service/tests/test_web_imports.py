# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
import sys

from bugbug.model import CommentModel
from bugbug.models import get_model_class
from bugbug_http import jobs, model_names, models

# bugbug_http.models, which only the RQ workers run, and the libraries only they need, which would
# add a couple hundred MB to each web process.
WORKER_MODULES = [
    "bugbug_http.models",
    "numpy",
    "pandas",
    "scipy",
    "sklearn",
    "xgboost",
    "imblearn",
]


def test_web_does_not_import_worker_modules() -> None:
    # In a new interpreter, as the tests themselves import them.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, bugbug_http.app; "
            f"print(' '.join(m for m in {WORKER_MODULES!r} if m in sys.modules))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.split() == []


def test_model_names() -> None:
    assert [
        model_name
        for model_name in model_names.MODELS_NAMES
        if issubclass(get_model_class(model_name), CommentModel)
    ] == model_names.COMMENT_MODELS

    assert model_names.PUSH_CLASSIFIERS.keys() <= set(model_names.MODELS_NAMES)


def test_job_names() -> None:
    names = {
        name: value for name, value in vars(jobs).items() if not name.startswith("_")
    }
    assert names
    for name, value in names.items():
        assert value == name
        assert callable(getattr(models, name))
