#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


import datetime
import logging
import threading
import time
from datetime import timedelta
from typing import Callable, Dict, Generic, TypeVar

import requests
from libmozdata import config
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

LOGGER = logging.getLogger()


def get_bugzilla_http_client():
    """ Copied from libmozdata.connection.Connection and libmozdata.bugzilla.Bugzilla
    """
    http_client = requests.Session()
    status_forcelist = [429]
    retries = Retry(total=256, backoff_factor=1, status_forcelist=status_forcelist)
    bugzilla_url = config.get("Bugzilla", "URL", "https://bugzilla.mozilla.org")
    bugzilla_api_url = bugzilla_url + "/rest/bug"
    http_client.mount(bugzilla_url, HTTPAdapter(max_retries=retries))

    return http_client, bugzilla_api_url


def get_hgmo_stack(branch: str, revision: str) -> list:
    """Load descriptions of patches in the stack for a given revision"""
    url = f"https://hg.mozilla.org/{branch}/json-automationrelevance/{revision}"
    r = requests.get(url)
    r.raise_for_status()
    return r.json()["changesets"]


# A simple TTL cache to use with models. Because we expect the number of models
# in the service to not be very large, simplicity of implementation is
# preferred to algorithmic efficiency of operations.
#
# Called an 'Idle' TTL cache because TTL of items is reset after every get
Key = TypeVar("Key")
Value = TypeVar("Value")


class ReadthroughTTLCache(Generic[Key, Value]):
    def __init__(self, ttl: timedelta, load_item_function: Callable[[Key], Value]):
        self.ttl = ttl
        self.load_item_function = load_item_function
        self.items_last_accessed: Dict[Key, datetime.datetime] = {}
        self.items_storage: Dict[Key, Value] = {}

    def __contains__(self, key):
        return key in self.items_storage

    def get(self, key):
        item = None
        if item in self.items_storage:
            item = self.items_storage[key]
        else:
            item = self.load_item_function(key)
            # Cache the item only if it was last accessed within the past TTL seconds
            # Note that all entries in items_last_accessed are purged if item was not
            # accessed in the last TTL seconds.
            if key in self.items_last_accessed:
                LOGGER.info(
                    f"Storing item with the following key in readthroughcache: {key}"
                )
                self.items_storage[key] = item

        self.items_last_accessed[key] = datetime.datetime.now()
        return item

    def force_store(self, key):
        LOGGER.info(f"Storing item with the following key in readthroughcache: {key}")
        self.items_storage[key] = self.load_item_function(key)
        self.items_last_accessed[key] = datetime.datetime.now()

    def purge_expired_entries(self):
        print("thread did a thing")
        purge_entries_before = datetime.datetime.now() - self.ttl
        for key, time_last_touched in list(self.items_last_accessed.items()):
            if time_last_touched < purge_entries_before:
                LOGGER.info(
                    f"Evicting item with the following key from readthroughcache: {key}"
                )
                del self.items_last_accessed[key]
                del self.items_storage[key]

    def start_ttl_thread(self):
        def purge_expired_entries_with_wait():
            while True:
                self.purge_expired_entries()
                time.sleep(self.ttl.total_seconds())

        thread = threading.Thread(target=purge_expired_entries_with_wait)
        thread.setDaemon(True)
        thread.start()
