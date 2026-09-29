# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Perf task labels: label parsing, grouping hierarchy and exclusions.

A task label such as ``test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon``
is described at four levels: the label, the platform-independent test identity,
the family (kind-file entry) and the category (what the test measures). The
platform is a separate axis.
"""

import re

from bugbug.perf.label_policy import (
    APPLICATIONS,
    CATEGORY_RULES,
    EXCLUDED_FRAMEWORKS,
    EXCLUDED_TEST_MARKERS,
    EXCLUDED_VARIANTS,
    FIREFOX_APPLICATIONS,
    PERF_LABEL_MARKERS,
    PLATFORM_RENAMES,
)

LABEL_RE = re.compile(r"^test-(?P<platform>[^/]+)/(?P<build>[^-]+)-(?P<test>.+)$")


def split_task_label(label: str) -> tuple[str, str, str] | None:
    """Split a test task label into (platform, build type, test name)."""
    match = LABEL_RE.match(label)
    if match is None:
        return None
    return match.group("platform"), match.group("build"), match.group("test")


def normalize_platform(platform: str) -> str:
    """Map a platform to its current name so history survives OS upgrades.

    Drops the retired ``-qr`` suffix and applies :data:`PLATFORM_RENAMES`, e.g.
    ``linux1804-64-shippable`` becomes ``linux2404-64-shippable``.
    """
    platform = platform.replace("-qr", "")
    for old, new in PLATFORM_RENAMES.items():
        if platform == old or platform.startswith(f"{old}-"):
            if not platform.startswith(new):
                platform = new + platform[len(old) :]
            break
    return platform


def normalize_task_label(label: str) -> str:
    """Rewrite a task label with its platform normalized; other labels pass through."""
    parts = split_task_label(label)
    if parts is None:
        return label
    platform, build, test = parts
    return f"test-{normalize_platform(platform)}/{build}-{test}"


def get_platform_family(platform: str) -> str:
    """The OS behind a platform: linux, windows, macosx, android or other."""
    if "android" in platform:
        return "android"
    for family in ("linux", "windows", "macosx"):
        if platform.startswith(family):
            return family
    return "other"


def is_perf_task_label(label: str) -> bool:
    """Whether a task label belongs to one of the performance harnesses."""
    return any(marker in label for marker in PERF_LABEL_MARKERS)


def split_test_name(test_name: str) -> dict[str, str | None]:
    """Split a platform-independent test name into family, application, subtest, variant.

    The family is the kind-file entry the test comes from (``browsertime-tp6``,
    ``talos-g1``, ``awsy``), the application is the browser under test, and the
    variant marks diagnostic flavours such as profiling runs.
    """
    variant = None
    if "-profiling" in test_name:
        variant = "profiling"
    elif test_name.endswith("-swr"):
        variant = "swr"

    tokens = test_name.split("-")
    harness = tokens[0]
    application = None
    app_index = None
    app_len = 0
    for i in range(1, len(tokens)):
        for app in APPLICATIONS:
            n = len(app.split("-"))
            if "-".join(tokens[i : i + n]) == app:
                application, app_index, app_len = app, i, n
                break
        if app_index is not None:
            break

    if harness in ("browsertime", "raptor"):
        if app_index is None:
            family, subtest = test_name, None
        else:
            subtest = "-".join(tokens[app_index + app_len :]) or None
            family = "-".join(tokens[:app_index]) if app_index > 1 else harness
            if subtest and subtest.startswith("youtube-playback"):
                family = f"{harness}-youtube-playback"
    elif harness == "talos":
        family = re.sub(r"-(profiling|swr)$", "", test_name)
        subtest = None
        application = "firefox"
    elif harness == "awsy":
        family, subtest, application = (
            "awsy",
            test_name[len("awsy-") :] or None,
            "firefox",
        )
    elif harness == "perftest":
        family = "perftest-startup" if "startup" in test_name else "perftest"
        subtest = None
        application = application or "firefox"
    else:
        family, subtest = harness, None

    if variant == "profiling":
        family = re.sub(r"-(native-)?profiling$", "", family)

    return {
        "family": family,
        "application": application,
        "subtest": subtest,
        "variant": variant,
    }


def get_test_category(test_name: str) -> str:
    """What the test measures: the first matching :data:`CATEGORY_RULES` entry, else other."""
    for pattern, category in CATEGORY_RULES:
        if pattern.search(test_name):
            return category
    return "other"


def get_runnable_identity(label: str) -> dict[str, str | None]:
    """Describe a normalized perf task label at every level models score at.

    ``test_name`` is the platform-independent identity, ``family`` the kind
    entry, ``category`` what the test measures, and ``get_platform_family`` the OS.
    """
    normalized = normalize_task_label(label)
    parts = split_task_label(normalized)
    if parts is None:
        platform, build, test = None, None, normalized
        family_of_platform = "other"
    else:
        platform, build, test = parts
        family_of_platform = get_platform_family(platform)
    grouping = split_test_name(test)
    return {
        "label": normalized,
        "platform": f"{platform}/{build}" if platform else None,
        "build_type": build,
        "platform_family": family_of_platform,
        "test_name": test,
        "family": grouping["family"],
        "category": get_test_category(test),
        "application": grouping["application"],
        "variant": grouping["variant"],
    }


def get_exclusion_reason(
    label: str, framework: str | None = None, application: str | None = None
) -> str | None:
    """Why a runnable is out of scope for Firefox perf test selection, or None."""
    if framework in EXCLUDED_FRAMEWORKS:
        return "framework"
    identity = get_runnable_identity(label)
    if identity["variant"] in EXCLUDED_VARIANTS:
        return "variant"
    test_name = identity["test_name"] or ""
    if any(marker in test_name for marker in EXCLUDED_TEST_MARKERS):
        return "canary"
    app = identity["application"] or application or None
    if app and app not in FIREFOX_APPLICATIONS:
        return "application"
    return None
