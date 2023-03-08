# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
from logging import INFO, basicConfig, getLogger

basicConfig(level=INFO)
logger = getLogger(__name__)

with open("VERSION", "r") as f:
    version = f.read().rstrip()

try:
    p = subprocess.run(
        ["git", "describe", "--abbrev=0", "--tags"], check=True, capture_output=True
    )
except subprocess.CalledProcessError as e:
    logger.info("%s failed with return code %d", e.cmd, e.returncode)
    logger.info("stdout:")
    logger.info(e.stdout)
    logger.info("stderr:")
    logger.info(e.stderr)
    raise Exception("Failure while getting latest tag")

cur_tag = p.stdout.decode("utf-8")[1:].rstrip()

assert (
    version == cur_tag
), f"Version in the VERSION file ({version}) should be the same as the current tag ({cur_tag})"
