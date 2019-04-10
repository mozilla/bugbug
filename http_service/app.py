# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os

from flask import Flask, current_app, jsonify

from bugbug import bugzilla

from .models import load_model

application = Flask(__name__)

bugzilla.set_token(os.environ.get("BUGBUG_BUGZILLA_TOKEN"))


def get_model(model):
    attribute = f"bugbug_model_{model}"

    if not hasattr(current_app, attribute):
        model = load_model(model)
        setattr(current_app, attribute, model)

    return getattr(current_app, attribute, model)


@application.route("/<model>/predict/<bug_id>")
def model_prediction(model, bug_id):
    bugs = bugzilla._download([bug_id])
    model = get_model(model)
    probs = model.classify(list(bugs.values()), True)
    indexes = probs.argmax(axis=-1)
    suggestions = model.clf._le.inverse_transform(indexes)

    data = {
        "probs": probs.tolist(),
        "indexes": indexes.tolist(),
        "suggestions": suggestions.tolist(),
    }

    return jsonify(**data)
