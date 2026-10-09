# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import responses

from bugbug import redash


@responses.activate
def test_redash_client_polls_job() -> None:
    base = "https://redash.example"
    responses.add(
        responses.POST,
        f"{base}/api/query_results",
        json={"job": {"id": "j1", "status": 1, "query_result_id": None, "error": ""}},
    )
    responses.add(
        responses.GET,
        f"{base}/api/jobs/j1",
        json={"job": {"id": "j1", "status": 3, "query_result_id": 7, "error": ""}},
    )
    responses.add(
        responses.GET,
        f"{base}/api/query_results/7.json",
        json={"query_result": {"data": {"rows": [{"id": 1}], "columns": []}}},
    )
    client = redash.RedashClient("key", base_url=base, poll_interval=0)
    assert client.execute(89, "SELECT 1") == [{"id": 1}]
    assert responses.calls[0].request.headers["Authorization"] == "Key key"
