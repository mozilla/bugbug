# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip

import orjson

from bugbug_http.app import API_TOKEN


def retrieve_compressed_reponse(response):
    # Response is of type "<class 'flask.wrappers.Response'>" -  Flask Client's  Response
    # Not applicable for "<class 'requests.models.Response'> "
    if response.headers["Content-Encoding"] == "gzip":
        return orjson.loads(gzip.decompress(response.data))
    return response.json


def test_patch_schedules_post_with_cache(client, add_result, jobs):
    """Test that POST requests with cached results still work correctly.

    This test verifies the fix for the issue where sending a POST request
    with a patch body to a previously cached endpoint would fail with
    IncompleteRead error because the response was sent before consuming
    the request body.
    """
    base_rev = "abc123"
    patch_hash = "def456"
    patch_content = """diff --git a/test.txt b/test.txt
--- a/test.txt
+++ b/test.txt
@@ -1,1 +1,2 @@
 line 1
+line 2
"""

    # First POST request - should queue the job
    rv = client.post(
        f"/patch/{base_rev}/{patch_hash}/schedules",
        data=patch_content.encode("utf-8"),
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 202
    assert rv.json == {"ready": False}

    # Simulate job completion with cached result
    result = {
        "groups": ["foo/mochitest.ini"],
        "tasks": ["test-linux/opt-mochitest-1"],
    }
    keys = next(iter(jobs.values()))
    add_result(keys[0], result)

    # Second POST request with same parameters - should return cached result
    # This is where the bug would occur - sending response before reading body
    rv = client.post(
        f"/patch/{base_rev}/{patch_hash}/schedules",
        data=patch_content.encode("utf-8"),
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 200
    assert retrieve_compressed_reponse(rv) == result


def test_patch_schedules_get_with_cache(client, add_result, jobs):
    """Test that GET requests work correctly with cached results."""
    base_rev = "abc123"
    patch_hash = "def456"
    patch_content = """diff --git a/test.txt b/test.txt
--- a/test.txt
+++ b/test.txt
@@ -1,1 +1,2 @@
 line 1
+line 2
"""

    # First POST request to submit the patch
    rv = client.post(
        f"/patch/{base_rev}/{patch_hash}/schedules",
        data=patch_content.encode("utf-8"),
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 202
    assert rv.json == {"ready": False}

    # Simulate job completion with cached result
    result = {
        "groups": ["foo/mochitest.ini"],
        "tasks": ["test-linux/opt-mochitest-1"],
    }
    keys = next(iter(jobs.values()))
    add_result(keys[0], result)

    # GET request should return cached result
    rv = client.get(
        f"/patch/{base_rev}/{patch_hash}/schedules",
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 200
    assert retrieve_compressed_reponse(rv) == result


def test_patch_schedules_get_without_cache(client):
    """Test that GET requests without cache return 404."""
    base_rev = "abc123"
    patch_hash = "def456"

    # GET request without submitting patch should return 404
    rv = client.get(
        f"/patch/{base_rev}/{patch_hash}/schedules",
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 404
    assert rv.json == {"error": "Patch not submitted yet"}
