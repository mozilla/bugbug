# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_model_name() -> str | None:
    pr_description = os.environ.get("PR_DESCRIPTION")
    if not pr_description:
        logger.error("The PR_DESCRIPTION environment variable does not exist")
        return None

    match = re.search(r"Train on Taskcluster:\s+([a-z_1-9]+)", pr_description)
    if not match:
        logger.error(
            "Could not identify the model name using the 'Train on Taskcluster' keyword from the Pull Request description"
        )
        return None

    model_name = match.group(1)

    return model_name


def main():
    model = get_model_name()
    if model:
        print(model)


if __name__ == "__main__":
    main()
