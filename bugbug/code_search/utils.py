# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from datetime import datetime
from functools import lru_cache

import requests
import tiktoken
from urllib3.util import Retry

LLM = "gpt-4o"

try:
    tokenizer = tiktoken.encoding_for_model(LLM)
except KeyError:
    FALLBACK_ENCODING = "cl100k_base"
    logging.info(
        "Tokenizer couldn't be found for %s, falling back to %s",
        LLM,
        FALLBACK_ENCODING,
    )
    tokenizer = tiktoken.get_encoding(FALLBACK_ENCODING)


def count_tokens(text):
    return len(tokenizer.encode(text))


def commit_available(commit_hash):
    r = requests.get(f"https://hg.mozilla.org/mozilla-unified/json-rev/{commit_hash}")
    return r.ok


def get_file(commit_hash, path):
    r = requests.get(
        f"https://hg.mozilla.org/mozilla-unified/raw-file/{commit_hash}/{path}"
    )
    r.raise_for_status()
    return r.text


def find_base_commit_hash(diff):
    try:
        base_commit_hash = diff["refs"]["base"]["identifier"]
        if commit_available(base_commit_hash):
            return base_commit_hash
    except KeyError:
        pass

    end_date = datetime.fromtimestamp(diff["dateCreated"])
    start_date = datetime.fromtimestamp(diff["dateCreated"] - 86400)
    end_date_str = end_date.strftime("%Y-%m-%d %H:%M:%S")
    start_date_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
    r = requests.get(
        f"https://hg.mozilla.org/mozilla-central/json-pushes?startdate={start_date_str}&enddate={end_date_str}&version=2&tipsonly=1"
    )
    pushes = r.json()["pushes"]
    closest_push = None
    for push_id, push in pushes.items():
        if diff["dateCreated"] - push["date"] < 0:
            continue

        if (
            closest_push is None
            or diff["dateCreated"] - push["date"]
            < diff["dateCreated"] - closest_push["date"]
        ):
            closest_push = push

    return closest_push["changesets"][0]


# To retrieve 20 lines before a given hunk, need to pass the first line of the hunk in the "before" state (e.g.
# diff_context(diff, "testing/modules/XPCShellContentUtils.sys.mjs", "up", 416, 20) for the first hunk in
# https://phabricator.services.mozilla.com/D199248?id=811740).
#
# To retrieve 20 lines after a given hunk, need to pass the last line of the hunk in the "before" state (e.g.
# diff_context(diff, "testing/modules/XPCShellContentUtils.sys.mjs", "up", 439, 20) for the first hunk in
# https://phabricator.services.mozilla.com/D199248?id=811740).
def diff_context(diff, path, direction, line, context_lines):
    base_commit_hash = find_base_commit_hash(diff)

    file_lines = get_file(
        base_commit_hash if base_commit_hash is not None else "tip", path
    ).split("\n")

    if direction == "up":
        return file_lines[line - context_lines - 1 : line]
    elif direction == "down":
        return file_lines[line - 1 : line + context_lines]


@lru_cache(maxsize=None)
def get_session(name: str) -> requests.Session:
    session = requests.Session()

    retry = Retry(total=9, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])

    # Default HTTPAdapter uses 10 connections. Mount custom adapter to increase
    # that limit. Connections are established as needed, so using a large value
    # should not negatively impact performance.
    http_adapter = requests.adapters.HTTPAdapter(
        pool_connections=50, pool_maxsize=50, max_retries=retry
    )
    session.mount("https://", http_adapter)
    session.mount("http://", http_adapter)

    return session
