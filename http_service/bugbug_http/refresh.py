#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Refresh the data about the current tasks, build platforms and commits that the worker retrieved at boot.

Workers can run for weeks, while the tasks of the task graph and the files built on each platform
change (e.g. platform migrations), and new commits land (used by the features of the test selection
models). This runs next to the worker, in the same container and directory: it rewrites the worker's
files (atomically) and databases (LMDB, whose readers see consistent snapshots), and jobs read them
when they run.
"""

import logging
import os
import time

from bugbug_http import boot
from bugbug_http.sentry import setup_sentry

logging.basicConfig(level=logging.INFO)

if os.environ.get("SENTRY_DSN"):
    setup_sentry(dsn=os.environ.get("SENTRY_DSN"))


def main() -> None:
    # Mining commits uses all the CPUs: don't slow the jobs down.
    os.nice(10)

    while True:
        time.sleep(boot.REFRESH_INTERVAL)
        boot.refresh_data()


if __name__ == "__main__":
    main()
