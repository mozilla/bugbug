# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Client for STMO (sql.telemetry.mozilla.org), Mozilla's Redash instance.

Saved queries run by id with parameters and ad-hoc SQL runs on a data source;
both go through the Redash jobs API and share its polling.
"""

import os
import time
from typing import Any

import requests

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
    """Run saved queries and ad-hoc SQL on STMO through the Redash jobs API."""

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
        """Run the SQL ``query`` on a data source and return its rows."""
        response = self.session.post(
            f"{self.base_url}/api/query_results",
            json={"data_source_id": data_source_id, "query": query, "max_age": 0},
            timeout=120,
        )
        return self._rows(response, timeout)

    def query(
        self,
        query_id: int,
        parameters: dict[str, Any],
        max_age: int = 1800,
        timeout: float = 1800,
    ) -> list[dict[str, Any]]:
        """Run the saved query ``query_id`` with ``parameters`` and return its rows.

        A stored result at most ``max_age`` seconds old is returned instead of
        running the query again.
        """
        response = self.session.post(
            f"{self.base_url}/api/queries/{query_id}/results",
            json={"parameters": parameters, "max_age": max_age},
            timeout=120,
        )
        return self._rows(response, timeout)

    def _rows(
        self, response: requests.Response, timeout: float
    ) -> list[dict[str, Any]]:
        """Return the rows of a query submission, waiting for its job if needed."""
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


def query_redash(query_id: int, parameters: dict[str, Any]) -> list[dict[str, Any]]:
    """Run a saved STMO query with the configured API key."""
    return RedashClient(get_redash_api_key()).query(query_id, parameters)
