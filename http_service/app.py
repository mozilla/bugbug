# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
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


def classify_bug(model_name, bug_ids):
    bugs = bugzilla._download(bug_ids)
    model = get_model(model_name)
    probs = model.classify(list(bugs.values()), True)
    indexes = probs.argmax(axis=-1)
    suggestions = model.clf._le.inverse_transform(indexes)

    data = {
        "probs": probs.tolist(),
        "indexes": indexes.tolist(),
        "suggestions": suggestions.tolist(),
    }

    return data


@application.route("/<model_name>/predict/<bug_id>")
def model_prediction(model_name, bug_id):
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify({"message": "Error, missing X-API-KEY"}), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    data = classify_bug(model_name, [bug_id])

    return jsonify(**data)


@application.route("/<model_name>/predict/batch", methods=["POST"])
def batch_prediction(model_name):
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify({"message": "Error, missing X-API-KEY"}), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    # TODO Check is JSON is valid and validate against a request schema
    batch_body = json.loads(request.data)

    print("BATCH BODY", request.data, batch_body)
    bugs = batch_body["bugs"]

    data = classify_bug(model_name, bugs)

    return jsonify(**data)
