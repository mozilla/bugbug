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

LOGGER = logging.getLogger()

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

    def get(self, key, force_store=False):
        store_item = force_store
        if key in self.items_storage:
            item = self.items_storage[key]
        else:
            item = self.load_item_function(key)
            # Cache the item only if it was last accessed within the past TTL seconds
            # Note that all entries in items_last_accessed are purged if item was not
            # accessed in the last TTL seconds.
            if key in self.items_last_accessed:
                store_item = True

        self.items_last_accessed[key] = datetime.datetime.now()
        if store_item:
            LOGGER.info(
                f"Storing item with the following key in readthroughcache: {key}"
            )
            self.items_storage[key] = item

        return item

    def purge_expired_entries(self):
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
                time.sleep(self.ttl.total_seconds())
                self.purge_expired_entries()

        thread = threading.Thread(target=purge_expired_entries_with_wait)
        thread.setDaemon(True)
        thread.start()
