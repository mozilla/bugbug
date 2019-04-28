# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

# Non-relative imports might be brittle
from models import MODELS_NAMES, retrieve_model


def preload_models():
    for model_name in MODELS_NAMES:
        retrieve_model(model_name)


if __name__ == "__main__":
    preload_models()
