# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import datetime, timedelta
from unittest.mock import patch

from bugbug_http.utils import ReadthroughTTLCache


class MockDatetime:
    def __init__(self, mock_now):
        self.mock_now = mock_now

    def now(self):
        return self.mock_now

    def set_now(self, new_mock_now):
        self.mock_now = new_mock_now


class MockSleep:
    def __init__(self, mock_datetime):
        self.mock_datetime = mock_datetime

        # count variable used to ensure that context switch into
        # thread claling sleep occurred
        self.context_switch_total = 0

    def sleep(self, sleep_seconds):
        self.wakeup_time = self.mock_datetime.now() + timedelta(seconds=sleep_seconds)
        while self.mock_datetime.now() < self.wakeup_time:
            self.context_switch_total = (
                self.context_switch_total + 1 % 1000000
            )  # <- to make sure we don't take up too much memory


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
        cache.force_store("key_a")

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
        cache.force_store("key_a")

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
    cache = ReadthroughTTLCache(timedelta(hours=2), lambda x: "payload")
    cache.force_store("key_a")
    assert "key_a" in cache


def test_cache_thread():
    mockdatetime = MockDatetime(datetime(2019, 4, 1, 10))
    cache = ReadthroughTTLCache(timedelta(hours=2), lambda x: "payload")
    mocksleep = MockSleep(mockdatetime)
    with patch("datetime.datetime", mockdatetime):
        with patch("time.sleep", mocksleep.sleep):
            cache.force_store("key_a")

            cache.start_ttl_thread()

            # after one hour
            context_switch_count = mocksleep.context_switch_total
            mockdatetime.set_now(datetime(2019, 4, 1, 11))
            while context_switch_count == mocksleep.context_switch_total:
                pass
            assert "key_a" in cache

            # after two hours one minute
            context_switch_count = mocksleep.context_switch_total
            mockdatetime.set_now(datetime(2019, 4, 1, 12, 1))
            while context_switch_count == mocksleep.context_switch_total:
                pass
            assert "key_a" not in cache
