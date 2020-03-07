#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


import datetime
import threading
from datetime import timedelta
from typing import Dict

import requests
from libmozdata import config
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry


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
class IdleTTLCache:
    def __init__(self, ttl: timedelta):
        self.ttl = ttl
        self.items_last_touched: Dict[str, datetime.datetime] = {}
        self.items: Dict[str, object] = {}

    def __setitem__(self, key, item):
        self.items[key] = item
        self.items_last_touched[key] = datetime.datetime.now()

    def __getitem__(self, key):
        item = self.items[key]
        self.items_last_touched[key] = datetime.datetime.now()
        return item

    def __contains__(self, key):
        return key in self.items

    def purge_expired_entries(self):
        purge_entries_before = datetime.datetime.now() - self.ttl
        for (key, time_last_touched) in list(self.items_last_touched.items()):
            print(time_last_touched)
            if time_last_touched < purge_entries_before:
                del self.items_last_touched[key]
                del self.items[key]

    def start_ttl_thread(self):
        self.purge_expired_entries()
        threading.Timer(self.ttl.total_seconds(), self.start_ttl_thread).start()
