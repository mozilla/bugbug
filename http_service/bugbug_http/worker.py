#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
import sys

import sentry_sdk
from redis import Redis
from rq import Connection, Worker
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.rq import RqIntegration

import bugbug_http.boot
from bugbug import get_bugbug_version

if os.environ.get("SENTRY_DSN"):
    logging_integration = LoggingIntegration(
        # Default behaviour: INFO messages will be included as breadcrumbs
        level=logging.INFO,
        # Change default behaviour (ERROR messages events)
        event_level=logging.WARNING,
    )
    sentry_sdk.init(
        dsn=os.environ.get("SENTRY_DSN"),
        integrations=[RqIntegration(), logging_integration],
        release=get_bugbug_version(),
    )


def main():
    # Bootstrap the worker assets
    bugbug_http.boot.boot_worker()

    # Provide queue names to listen to as arguments to this script,
    # similar to rq worker
    redis_url = os.environ.get("REDIS_URL", "redis://localhost/0")
    redis_conn = Redis.from_url(redis_url)
    with Connection(connection=redis_conn):
        qs = sys.argv[1:] or ["default"]

        w = Worker(qs)
        w.work()


if __name__ == "__main__":
    main()
