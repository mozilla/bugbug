# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Every hand-maintained table that decides what a performance label means.

Which alert statuses count, which runnables are out of scope, how tests are grouped into families and
categories, and how retired platforms are folded. Functions that apply these
tables live in ``task_labels`` and ``regression_labeling``.
"""

import re

REPOSITORIES = ("autoland", "mozilla-central")

# Bump whenever any table in this file changes: they all change what a label means.
POLICY_VERSION = 1

SUMMARY_STATUS = {
    0: "untriaged",
    1: "downstream",
    2: "reassigned",
    3: "invalid",
    4: "improvement",
    5: "investigating",
    6: "wontfix",
    7: "fixed",
    8: "backedout",
    9: "infra",
}
ALERT_STATUS = {
    0: "untriaged",
    1: "downstream",
    2: "reassigned",
    3: "invalid",
    4: "acknowledged",
    5: "confirming",
}
CONFIRMED_SUMMARY_STATUSES = frozenset(
    ("investigating", "wontfix", "fixed", "backedout")
)
RESOLVED_SUMMARY_STATUSES = frozenset(("wontfix", "fixed", "backedout"))
EXCLUDED_ALERT_STATUSES = frozenset(("downstream", "invalid"))
# Harness-caused regressions stay in: a harness change is a repository change
# the scheduler must react to. Infra changes are not in any push.
EXCLUDED_SUMMARY_TAGS = ("infra",)

# Outcome of a regression, decided by the regression bug's Bugzilla resolution
# when one exists. FIXED and WONTFIX confirm it; WORKSFORME is kept because the
# metric did move even if nobody acted on it.
CONFIRMED_BUG_RESOLUTIONS = frozenset(("FIXED", "WONTFIX", "WORKSFORME"))
REJECTED_BUG_RESOLUTIONS = frozenset(("INVALID", "INACTIVE", "INCOMPLETE", "MOVED"))
# Outcomes that make a summary a positive label. "pending" and "rejected"
# summaries are neither positives nor negatives.
POSITIVE_OUTCOMES = frozenset(("concluded", "attributed"))

PERF_LABEL_MARKERS = ("-browsertime-", "-talos-", "-awsy", "-raptor-", "perftest-")

# Retired platforms folded into their current equivalent so a test keeps its
# history across a platform refresh. Maintained by hand.
PLATFORM_RENAMES = {
    "linux1804-64": "linux2404-64",
    "windows10-64": "windows11-64-24h2",
    "macosx1015-64": "macosx1470-64",
}

# Multi-token applications first so they win over their prefixes.
APPLICATIONS = (
    "chrome-m",
    "safari-tp",
    "custom-car",
    "cstm-car-m",
    "firefox",
    "chrome",
    "safari",
    "fenix",
    "geckoview",
    "refbrow",
    "focus",
)
FIREFOX_APPLICATIONS = frozenset(("firefox", "fenix", "geckoview", "refbrow", "focus"))
EXCLUDED_FRAMEWORKS = frozenset(("build_metrics",))
EXCLUDED_TEST_MARKERS = ("regression-tests",)
# Diagnostic flavours of a test, as detected by task_labels.split_test_name.
EXCLUDED_VARIANTS = ("profiling",)

# Ordered: the first matching rule wins.
CATEGORY_RULES = (
    (
        re.compile(r"youtube-playback|video-playback|media-seek|webcodecs|-power"),
        "media",
    ),
    (re.compile(r"network-bench|-upload|trr-performance|hev3|http3"), "network"),
    (re.compile(r"indexeddb"), "storage"),
    (re.compile(r"^awsy"), "memory"),
    (re.compile(r"^talos-damp"), "devtools"),
    (
        re.compile(
            r"startup|talos-other|sessionrestore|realworld-webextensions|talos-xperf|first-install"
        ),
        "startup",
    ),
    (
        re.compile(
            r"motionmark|webgl|talos-g1|talos-g4|talos-svgr|talos-bcv|pdfpaint|perf-reftest|unity"
        ),
        "graphics",
    ),
    (re.compile(r"pageload-benchmark|tp6-bench"), "page-load"),
    (
        re.compile(
            r"speedometer|jetstream|ares6|sunspider|matrix-react|stylebench|assorted-dom"
            r"|benchmark|dromaeo|talos-g3|talos-g5|kraken|wasm"
        ),
        "benchmark",
    ),
    (re.compile(r"responsiveness|tabswitch|talos-chrome|nav-bench"), "responsiveness"),
    (
        re.compile(r"tp6|tp7|talos-tp5o|speculat|throttled|browsertime-custom"),
        "page-load",
    ),
    (re.compile(r"ml-|tr8ns"), "ml"),
)
