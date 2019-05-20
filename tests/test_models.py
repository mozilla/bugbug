# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models import MODELS, get_model_class


def test_import_all_models():
    """ Try loading all defined models to ensure that their full qualified
    names are still good
    """

    for model_name in MODELS.keys():
        print("Try loading model", model_name)
        get_model_class(model_name)
