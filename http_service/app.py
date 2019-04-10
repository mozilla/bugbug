# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os

from flask import Flask, current_app, jsonify, request

from bugbug import bugzilla

from .models import load_model

API_TOKEN = "X-Api-Key"

application = Flask(__name__)

bugzilla.set_token(os.environ.get("BUGBUG_BUGZILLA_TOKEN"))

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()


def get_model(model):
    attribute = f"bugbug_model_{model}"

    if not hasattr(current_app, attribute):
        model = load_model(model)
        setattr(current_app, attribute, model)

    return getattr(current_app, attribute, model)


@application.route("/<model>/predict/<bug_id>")
def model_prediction(model, bug_id):
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify({"message": "Error, missing X-API-KEY"}), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

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
