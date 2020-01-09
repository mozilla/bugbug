#!/usr/bin/env python
# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


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
