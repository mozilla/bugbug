# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import sys
import time
from datetime import timedelta

from bugbug_http.utils import ReadthroughTTLCache

# to load bugbug_http.utils, when running from project root
sys.path.append(".")


def integration_test_cache_thread():
    TTL_SECONDS = 5
    cache = ReadthroughTTLCache(timedelta(seconds=TTL_SECONDS), lambda key: "payload")

    cache.force_store("test_key")

    cache.start_ttl_thread()
    time.sleep(TTL_SECONDS / 2)
    assert "test_key" in cache
    time.sleep(TTL_SECONDS)
    assert "test_key" not in cache
    print("test succeeded")


if __name__ == "__main__":
    integration_test_cache_thread()
    os._exit(0)
