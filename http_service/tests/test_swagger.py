# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.


def test_swagger(client):
    # The spec is generated from the docstrings of all routes, so a single
    # malformed docstring would make it fail for every endpoint.
    rv = client.get("/swagger")

    assert rv.status_code == 200
    spec = rv.json
    batch = spec["paths"]["/{model_name}/predict/broken_site_report/batch"]["post"]
    schema = batch["requestBody"]["content"]["application/json"]["schema"]
    assert schema["properties"]["reports"]["type"] == "array"

    # The results of the bugs are described by their component, not by its name.
    bugs = spec["paths"]["/{model_name}/predict/bugs/{branch}"]["post"]
    schema = bugs["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["properties"]["bugs"]["additionalProperties"] == {
        "$ref": "#/components/schemas/BugCommitsPrediction"
    }
    assert "BugCommitsPrediction" in spec["components"]["schemas"]
