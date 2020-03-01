# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import utils
from bugbug_http.models import MODELS_NAMES


def preload_models():
    for model_name in MODELS_NAMES:
        utils.download_model(model_name)


if __name__ == "__main__":
    preload_models()
