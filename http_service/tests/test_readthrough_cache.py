# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from bugbug_http.readthrough_cache import ReadthroughTTLCache

time_change_event = threading.Event()


class MockDatetime:
    def __init__(self, mock_now):
        self.mock_now = mock_now

    def now(self):
        return self.mock_now

    def set_now(self, new_mock_now):
        self.mock_now = new_mock_now
        time_change_event.set()


class MockSleep:
    def __init__(self, mock_datetime):
        self.mock_datetime = mock_datetime
        self.wakeups_count = 0

        # count variable used to ensure that context switch into
        # thread calling sleep occurred

    def sleep(self, sleep_seconds):
        self.wakeup_time = self.mock_datetime.now() + timedelta(seconds=sleep_seconds)
        while self.mock_datetime.now() < self.wakeup_time:
            time_change_event.wait()
            time_change_event.clear()
        self.wakeups_count += 1


class PurgeCountCache(ReadthroughTTLCache):
    def __init__(self, ttl, load_item_function):
        super().__init__(ttl, load_item_function)
        self.purge_count = 0

        def purge_then_increment(purge):
            purge()
            self.purge_count += 1

        super().purge_expired_entries = purge_then_increment(
            super().purge_expired_entries
        )


def test_doesnt_cache_unless_accessed_within_ttl():
    mockdatetime = MockDatetime(datetime(2019, 4, 1, 10))
    cache = ReadthroughTTLCache(timedelta(hours=4), lambda x: "payload")

    with patch("datetime.datetime", mockdatetime):
        cache.get("key_a")

        # after one hour
        mockdatetime.set_now(datetime(2019, 4, 1, 11))
        assert "key_a" not in cache

        # after two hours
        mockdatetime.set_now(datetime(2019, 4, 1, 12))
        cache.get("key_a")

        # after three hours
        mockdatetime.set_now(datetime(2019, 4, 1, 13))
        assert "key_a" in cache


def test_cache_purges_after_ttl():
    mockdatetime = MockDatetime(datetime(2019, 4, 1, 10))
    cache = ReadthroughTTLCache(timedelta(hours=2), lambda x: "payload")

    with patch("datetime.datetime", mockdatetime):
        cache.get("key_a", force_store=True)

        # after one hour
        mockdatetime.set_now(datetime(2019, 4, 1, 11))
        cache.purge_expired_entries()
        assert "key_a" in cache

        # after two hours one minute
        mockdatetime.set_now(datetime(2019, 4, 1, 12, 1))
        cache.purge_expired_entries()
        assert "key_a" not in cache


def test_cache_ttl_refreshes_after_get():
    mockdatetime = MockDatetime(datetime(2019, 4, 1, 10))
    cache = ReadthroughTTLCache(timedelta(hours=2), lambda x: "payload")

    with patch("datetime.datetime", mockdatetime):
        cache.get("key_a", force_store=True)

        # after one hour
        mockdatetime.set_now(datetime(2019, 4, 1, 11))
        cache.purge_expired_entries()
        assert "key_a" in cache
        assert cache.get("key_a") == "payload"

        # after three hours
        mockdatetime.set_now(datetime(2019, 4, 1, 13))
        cache.purge_expired_entries()
        assert "key_a" in cache

        # after three hours one minute
        mockdatetime.set_now(datetime(2019, 4, 1, 13, 1))
        cache.purge_expired_entries()
        assert "key_a" not in cache


def test_force_store():
    def with_spied_storage(cache):
        cache.storage_access_count = 0
        cache_getitem = cache.items_storage.__getitem__

        def spy_getitem(key):
            cache.storage_access_count += 1
            return cache_getitem(key)

        mock_items_storage = MagicMock()
        mock_items_storage.__getitem__.side_effect = spy_getitem
        mock_items_storage.__setitem__.side_effect = cache.items_storage.__setitem__
        mock_items_storage.__contains__.side_effect = cache.items_storage.__contains__
        cache.items_storage = mock_items_storage
        return cache

    cache = with_spied_storage(
        ReadthroughTTLCache(timedelta(hours=2), lambda x: "payload")
    )
    cache.get("key_a", force_store=True)

    assert "key_a" in cache
    assert cache.get("key_a") == "payload"
    assert cache.storage_access_count == 1


def test_cache_thread():
    def cache_with_purge_count(cache):
        cache.purge_count = 0
        purge = cache.purge_expired_entries

        def purge_then_increment():
            purge()
            cache.purge_count += 1

        cache.purge_expired_entries = purge_then_increment
        return cache

    mockdatetime = MockDatetime(datetime(2019, 4, 1, 10))
    cache = cache_with_purge_count(
        ReadthroughTTLCache(timedelta(hours=2), lambda x: "payload")
    )
    mocksleep = MockSleep(mockdatetime)
    with patch("datetime.datetime", mockdatetime):
        with patch("time.sleep", mocksleep.sleep):
            cache.get("key_a", force_store=True)
            cache.start_ttl_thread()

            # after one hour
            mockdatetime.set_now(datetime(2019, 4, 1, 11))
            assert "key_a" in cache
            assert mocksleep.wakeups_count == 0

            # after two hours one minute
            before_timechange_purge_count = cache.purge_count
            mockdatetime.set_now(datetime(2019, 4, 1, 12, 1))

            while cache.purge_count == before_timechange_purge_count:
                time.sleep(0)
            assert "key_a" not in cache
