# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Minimal client for ad-hoc SQL on STMO (sql.telemetry.mozilla.org, Redash)."""

import os
import time
from typing import Any

from bugbug import utils

REDASH_URL = "https://sql.telemetry.mozilla.org"

REDASH_JOB_PENDING = 1
REDASH_JOB_STARTED = 2
REDASH_JOB_SUCCESS = 3


def get_redash_api_key() -> str:
    api_key = os.environ.get("REDASH_API_KEY")
    if api_key:
        return api_key
    return utils.get_secret("REDASH_API_KEY")


class RedashClient:
    """Run ad-hoc SQL on an STMO data source through the Redash jobs API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = REDASH_URL,
        poll_interval: float = 2.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.session = utils.get_session("redash")
        self.session.headers["Authorization"] = f"Key {api_key}"

    def execute(
        self, data_source_id: int, query: str, timeout: float = 1800
    ) -> list[dict[str, Any]]:
        response = self.session.post(
            f"{self.base_url}/api/query_results",
            json={"data_source_id": data_source_id, "query": query, "max_age": 0},
            timeout=120,
        )
        response.raise_for_status()
        body = response.json()

        if "query_result" in body:
            return body["query_result"]["data"]["rows"]

        job = body["job"]
        deadline = time.monotonic() + timeout
        while job["status"] in (REDASH_JOB_PENDING, REDASH_JOB_STARTED):
            if time.monotonic() > deadline:
                raise TimeoutError(f"Redash job {job['id']} did not finish in time")
            time.sleep(self.poll_interval)
            response = self.session.get(
                f"{self.base_url}/api/jobs/{job['id']}", timeout=120
            )
            response.raise_for_status()
            job = response.json()["job"]

        if job["status"] != REDASH_JOB_SUCCESS:
            raise RuntimeError(f"Redash job {job['id']} failed: {job.get('error')}")

        response = self.session.get(
            f"{self.base_url}/api/query_results/{job['query_result_id']}.json",
            timeout=300,
        )
        response.raise_for_status()
        return response.json()["query_result"]["data"]["rows"]
