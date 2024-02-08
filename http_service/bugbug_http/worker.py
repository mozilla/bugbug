#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import sys
from urllib.parse import urlparse

from redis import Redis
from rq import Connection, Worker
from sentry_sdk.integrations.rq import RqIntegration

import bugbug_http.boot
from bugbug_http.sentry import setup_sentry

if os.environ.get("SENTRY_DSN"):
    setup_sentry(dsn=os.environ.get("SENTRY_DSN"), integrations=[RqIntegration()])


def main():
    # Bootstrap the worker assets
    bugbug_http.boot.boot_worker()

    # Provide queue names to listen to as arguments to this script,
    # similar to rq worker
    url = urlparse(os.environ.get("REDIS_URL", "redis://localhost/0"))
    assert url.hostname is not None
    redis_conn = Redis(
        host=url.hostname,
        port=url.port if url.port is not None else 6379,
        password=url.password,
        ssl=True if url.scheme == "rediss" else False,
        ssl_cert_reqs=None,
    )
    with Connection(connection=redis_conn):
        qs = sys.argv[1:] or ["default"]

        w = Worker(qs)
        w.work()


if __name__ == "__main__":
    main()
