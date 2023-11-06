# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import logging
import os
import re
import subprocess
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_model_name(env_variable: str):
    try:
        text = os.environ[env_variable]
    except KeyError as e:
        logger.error("The environment variable %s does not exist", e)
        raise

    logger.info(f"Searching for model name from env_variable {env_variable}")

    match = re.search(r"Train on task cluster:\s+([a-z_1-9]+)", text)

    if not match:
        logger.error("There is no match found for keyword 'Train on task cluster:'")
        sys.exit(1)

    model = match.group(1)

    return model


def train(model: str):
    logger.info(f"Initiating {model} model training")

    try:
        subprocess.run(["bugbug-train", model], check=True, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logger.error(f"Error training the model: {e.stderr}")


def main():
    description = "Train a model on task cluster using the 'Train on taskcluster:' keyword passed in an environment variable"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument(
        "env_variable",
        help="The environment variable where the model name will be extracted from",
    )

    args = parser.parse_args()

    model = get_model_name(args.env_variable)
    train(model)


if __name__ == "__main__":
    main()
