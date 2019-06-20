#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import sys
from os.path import dirname, join

from redis import Redis
from rq import Connection, Worker

import http_service.models  # Need to import at with the same name

# Preload libraries
sys.path.insert(0, dirname(join(__file__, "..")))

http_service.models.preload_models()

# Provide queue names to listen to as arguments to this script,
# similar to rq worker
redis_url = os.environ.get("REDIS_URL", "redis://localhost/0")
redis_conn = Redis.from_url(redis_url)
with Connection(connection=redis_conn):
    qs = sys.argv[1:] or ["default"]

    w = Worker(qs)
    w.work()
