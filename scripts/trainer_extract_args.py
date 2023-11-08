# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
import re
import sys

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_model_name():
    text = os.environ.get("PR_DESCRIPTION")

    match = re.search(r"Train on Taskcluster:\s+([a-z_1-9]+)", text)

    if not match:
        logger.error("There is no match found for keyword 'Train on Taskcluster:'")
        sys.exit(1)

    model = match.group(1)

    return model


def main():
    model = get_model_name()
    print(model)


if __name__ == "__main__":
    main()
