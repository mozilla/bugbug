# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip
from unittest.mock import patch

import orjson

from bugbug_http.app import API_TOKEN


def retrieve_compressed_reponse(response):
    # Response is of type "<class 'flask.wrappers.Response'>" -  Flask Client's  Response
    # Not applicable for "<class 'requests.models.Response'> "
    if response.headers["Content-Encoding"] == "gzip":
        return orjson.loads(gzip.decompress(response.data))
    return response.json


def test_patch_schedules_post_with_cache(client, add_result, jobs):
    """Test that POST requests with cached results consume the request body.

    This test verifies the fix for the issue where sending a POST request
    with a patch body to a previously cached endpoint would fail with
    IncompleteRead error because the response was sent before consuming
    the request body.

    The test verifies that request.data is accessed before compress_response
    is called when there's cached data for a POST request.
    """
    from bugbug_http import app

    base_rev = "abc123"
    patch_hash = "def456"
    patch_content = """
From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: Test User <test@example.com>
Date: Mon Nov 17 14:58:22 2025
Subject: [PATCH] Uncommitted changes
---

diff --git a/test.txt b/test.txt
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

    # Wrap compress_response to track if request.data was accessed before it
    original_compress_response = app.compress_response
    request_data_accessed_before_compress = False

    def tracking_compress_response(*args, **kwargs):
        nonlocal request_data_accessed_before_compress
        # Check if we're in a request context and if data was accessed
        try:
            from flask import has_request_context, request

            if has_request_context() and request.method == "POST":
                # Access the internal Flask request object to check if body was consumed
                # The _cached_data attribute is set when request.data is accessed
                if hasattr(request, "_cached_data"):
                    request_data_accessed_before_compress = True
        except Exception:
            pass
        return original_compress_response(*args, **kwargs)

    with patch(
        "bugbug_http.app.compress_response", side_effect=tracking_compress_response
    ):
        # Second POST request with same parameters - should return cached result
        # This is where the bug would occur - sending response before reading body
        rv = client.post(
            f"/patch/{base_rev}/{patch_hash}/schedules",
            data=patch_content.encode("utf-8"),
            headers={API_TOKEN: "test"},
        )

        assert rv.status_code == 200
        assert retrieve_compressed_reponse(rv) == result

        # Verify that request.data was accessed before compress_response was called
        assert request_data_accessed_before_compress, (
            "request.data was not accessed before compress_response for POST request with cached result"
        )


def test_patch_schedules_get_with_cache(client, add_result, jobs):
    """Test that GET requests work correctly with cached results."""
    base_rev = "abc123"
    patch_hash = "def456"
    patch_content = """
From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: Test User <test@example.com>
Date: Mon Nov 17 14:58:22 2025
Subject: [PATCH] Uncommitted changes
---

diff --git a/test.txt b/test.txt
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


def test_patch_schedules_empty_patch(client):
    """Test that POST requests with truly empty patches return 400."""
    base_rev = "abc123"
    patch_hash = "empty123"

    # POST request with empty string should return 400
    rv = client.post(
        f"/patch/{base_rev}/{patch_hash}/schedules",
        data=b"",
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 400
    assert rv.json == {"error": "Empty patch"}


def test_patch_schedules_patch_without_diffs(client):
    """Test that POST requests with patches containing no diff content return 400."""
    base_rev = "abc123"
    patch_hash = "nodiff123"

    # Patch with headers but no actual diff content (like the example in the issue)
    patch_content = """
From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: Test User <test@example.com>
Date: Mon Nov 17 14:58:22 2025
Subject: [PATCH] Uncommitted changes
---


"""

    # POST request with patch that has no diffs should return 400
    rv = client.post(
        f"/patch/{base_rev}/{patch_hash}/schedules",
        data=patch_content.encode("utf-8"),
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 400
    assert rv.json == {"error": "Patch contains no diff content"}
