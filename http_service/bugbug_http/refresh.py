#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Refresh the data about the current tasks, build platforms and commits that the worker retrieved at boot.

Workers can run for weeks, while the tasks of the task graph and the files built on each platform
change (e.g. platform migrations), and new commits land (used by the features of the test selection
models), and tasks get renamed (whose labels the replies give for the selected configs, so they are
refreshed more often). This runs next to the worker, in the same container and directory: it rewrites the worker's
files (atomically) and databases (LMDB, whose readers see consistent snapshots), and jobs read them
when they run.
"""

import logging
import os
import time

from bugbug_http import boot
from bugbug_http.sentry import setup_sentry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if os.environ.get("SENTRY_DSN"):
    setup_sentry(dsn=os.environ.get("SENTRY_DSN"))


def main() -> None:
    # Mining commits uses all the CPUs: don't slow the jobs down.
    os.nice(10)

    # The test tasks among which configs are selected are updated much more often than the rest,
    # so that the selected configs run on tasks renamed since the latest backstop push, and the
    # commits more often too, so that few are left to mine by the jobs which need the latest ones.
    next_refresh = time.monotonic() + boot.REFRESH_INTERVAL
    next_commits_refresh = time.monotonic() + boot.COMMITS_REFRESH_INTERVAL
    while True:
        time.sleep(boot.CONFIG_SELECTION_TASKS_REFRESH_INTERVAL)
        if time.monotonic() >= next_refresh:
            # Including the commits and the config selection tasks.
            boot.refresh_data()
            next_refresh = time.monotonic() + boot.REFRESH_INTERVAL
            next_commits_refresh = time.monotonic() + boot.COMMITS_REFRESH_INTERVAL
            continue

        if time.monotonic() >= next_commits_refresh:
            try:
                boot.refresh_commits()
            except Exception:
                logger.exception("Failed to refresh the commits")
            next_commits_refresh = time.monotonic() + boot.COMMITS_REFRESH_INTERVAL

        try:
            boot.refresh_config_selection_tasks()
        except Exception:
            logger.exception("Failed to refresh the config selection tasks")


if __name__ == "__main__":
    main()
