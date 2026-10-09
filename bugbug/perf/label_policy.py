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

# Sheriffs attribute regressions to autoland pushes, and a mozilla-central push
# is a merge whose commit list repeats the autoland commits it merged, so
# including it only duplicates commits. Re-add it with --repository if needed.
REPOSITORIES = ("autoland",)

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
# build_metrics measures builds, not tests. js-bench is the SpiderMonkey shell
# benchmarks, run from source-test tasks the JS team schedules; two regressions
# in six months and a separate label shape make them not worth modeling yet.
EXCLUDED_FRAMEWORKS = frozenset(("build_metrics", "js-bench"))
# Synthetic canaries and the harness sample tests.
EXCLUDED_TEST_MARKERS = (
    "regression-tests",
    "browsertime-sample",
    "sample-python-support",
)
# Diagnostic flavours of a test, as detected by task_labels.split_test_name.
EXCLUDED_VARIANTS = ("profiling",)

# What a test measures. Every matching rule applies, so a test can carry
# several categories (a YouTube power test is media and power); the first match
# is its primary category, so rules are ordered from specific to general.
# Patterns run against the platform-independent test name.
CATEGORY_RULES = (
    (
        re.compile(
            r"(^|-)ml-|-ml$|tr8ns|mlsuggest|semantichistory|smarttabgrouping"
            r"|smartwindow|linkpreview|speech-recognition"
        ),
        "ml",
    ),
    (re.compile(r"^talos-damp"), "devtools"),
    (re.compile(r"accessib|a11y"), "accessibility"),
    (
        re.compile(
            r"youtube-playback|video-playback|media-seek|media-playback|webcodecs"
            r"|media-capabilities|webaudio"
        ),
        "media",
    ),
    (
        re.compile(r"-power-|media-playback|background-resource|foreground-resource"),
        "power",
    ),
    (re.compile(r"^awsy|-dmd|background-resource|foreground-resource"), "memory"),
    (re.compile(r"indexeddb|perftest-places|service-worker"), "storage"),
    (
        re.compile(
            r"tp6|tp7|tp5|pageload|speculat|throttled|browsertime-custom"
            r"|process-switch|^talos-xperf|^talos-g5"
        ),
        "page-load",
    ),
    (
        re.compile(
            r"startup|sessionrestore|first-install|realworld-webextensions"
            r"|^talos-other|^talos-g5|^talos-xperf"
        ),
        "startup",
    ),
    (
        re.compile(
            r"motionmark|webgl|unity|twitch-animation|pdfpaint|perf-reftest"
            r"|^talos-g1|^talos-g4|^talos-svgr|^talos-bcv"
        ),
        "graphics",
    ),
    (
        re.compile(r"responsiveness|tabswitch|nav-bench|^talos-chrome|^talos-other"),
        "responsiveness",
    ),
    (
        re.compile(
            r"network-bench|-upload|trr-performance|busy-trr|hev3|http3|speculat"
        ),
        "network",
    ),
    (
        re.compile(
            r"benchmark|speedometer|jetstream|ares6|sunspider|matrix-react|stylebench"
            r"|assorted-dom|dromaeo|kraken|^talos-g3|wasm-godot|wasm-misc"
            r"|motionmark|unity|twitch-animation|media-capabilities|webaudio"
        ),
        "benchmark",
    ),
)
