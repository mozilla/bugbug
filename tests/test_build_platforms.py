# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import io
import json
import re
import zipfile

import responses

from bugbug import build_platforms
from bugbug.build_platforms import ALL_PLATFORMS, BuildPlatforms

DESKTOP = {"linux", "macosx", "windows"}

# widget/cocoa is only built on macOS, browser/ on desktop, dom/ everywhere; the SSE2 code isn't built
# by the (aarch64) Android build.
BUILT = {
    "macosx": [
        "widget/cocoa/nsCocoaWindow.mm",
        "dom/base/Document.cpp",
        "dom/base/SSE2.cpp",
        "browser/base/content/browser.js",
    ],
    "windows": [
        "widget/windows/nsWindow.cpp",
        "dom/base/Document.cpp",
        "dom/base/SSE2.cpp",
        "browser/base/content/browser.js",
    ],
    "linux": [
        "widget/gtk/nsWindow.cpp",
        "dom/base/Document.cpp",
        "dom/base/SSE2.cpp",
        "browser/base/content/browser.js",
    ],
    "android": ["widget/android/nsWindow.cpp", "dom/base/Document.cpp"],
}
# gtests, which the Android job doesn't build.
for _platform in DESKTOP:
    BUILT[_platform] = BUILT[_platform] + ["dom/media/gtest/TestAudioBuffers.cpp"]


def test_platforms() -> None:
    def platforms(path, built=BUILT):
        return set(BuildPlatforms(built).platforms(path))

    # Listed files.
    assert platforms("widget/cocoa/nsCocoaWindow.mm") == {"macosx"}
    assert platforms("dom/base/Document.cpp") == set(ALL_PLATFORMS)
    assert platforms("browser/base/content/browser.js") == DESKTOP
    # Android tests run on another architecture than the one the files list comes from.
    assert platforms("dom/base/SSE2.cpp") == set(ALL_PLATFORMS)
    # Not listed: by the closest directory with built files.
    assert platforms("widget/cocoa/NewFile.mm") == {"macosx"}
    assert platforms("browser/base/content/test/browser_test.js") == DESKTOP
    assert platforms("widget/README.md") == set(ALL_PLATFORMS)
    assert platforms("README.md") == set(ALL_PLATFORMS)
    # Files of types no build lists (e.g. inputs of code generation) aren't restricted by directory.
    assert platforms("browser/base/content/metrics.yaml") == set(ALL_PLATFORMS)
    assert platforms("widget/cocoa/moz.build") == set(ALL_PLATFORMS)
    # Android runs gtests, although its job doesn't build them.
    assert platforms("dom/media/gtest/TestAudioBuffers.cpp") == set(ALL_PLATFORMS)
    assert platforms("dom/media/gtest/TestNew.cpp") == set(ALL_PLATFORMS)
    # Platforms whose files are unknown (or empty) might build anything.
    without_linux = {p: f for p, f in BUILT.items() if p != "linux"}
    assert platforms("widget/cocoa/nsCocoaWindow.mm", without_linux) == {
        "linux",
        "macosx",
    }
    assert platforms("widget/cocoa/nsCocoaWindow.mm", {**BUILT, "windows": []}) == {
        "macosx",
        "windows",
    }


def test_find_platforms_for_paths() -> None:
    find = BuildPlatforms(BUILT).find_platforms_for_paths
    assert find(["widget/cocoa/nsCocoaWindow.mm"]) == {"macosx"}
    assert find(["widget/cocoa/nsCocoaWindow.mm", "widget/gtk/nsWindow.cpp"]) == {
        "linux",
        "macosx",
    }
    assert find([]) == ALL_PLATFORMS


def mock_artifacts(files_by_job: dict[str, list[str]]) -> None:
    for job, files in files_by_job.items():
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as z:
            for name in files:
                z.writestr(name, b"analysis")
            z.writestr("__GENERATED__/dist/include/generated.h", b"analysis")
        content = data.getvalue()
        url = build_platforms.ARTIFACT_URL.format(
            job=job, artifact="target.mozsearch-index.zip"
        )

        def get_range(request, content=content):
            start, end = map(
                int, re.match(r"bytes=(\d+)-(\d+)", request.headers["Range"]).groups()
            )
            return (206, {}, content[start : end + 1])

        responses.add(
            responses.HEAD, url, headers={"Content-Length": str(len(content))}
        )
        responses.add_callback(responses.GET, url, callback=get_range)
        responses.add(
            responses.GET,
            build_platforms.ARTIFACT_URL.format(job=job, artifact="chrome-map.json"),
            body=json.dumps(
                [
                    {},
                    {},
                    {
                        "dist/bin/browser/browser.js": [f"{job}/browser.js", {}],
                        # Test files installed for a harness aren't built.
                        "_tests/reftest/crashtests": [
                            "widget/cocoa/crashtests/*",
                            None,
                        ],
                    },
                    {},
                ]
            ),
        )


def test_retrieve_built_files() -> None:
    mock_artifacts(
        {
            "linux64-searchfox-debug": ["widget/gtk/nsWindow.cpp"],
            "macosx64-searchfox-debug": ["dom/base/SSE2.cpp"],
            "macosx64-aarch64-searchfox-debug": ["widget/cocoa/nsCocoaWindow.mm"],
            "win64-searchfox-debug": ["widget/windows/nsWindow.cpp"],
        }
    )
    # The Android files can't be retrieved.
    responses.add(
        responses.HEAD,
        build_platforms.ARTIFACT_URL.format(
            job="android-aarch64-searchfox-debug",
            artifact="target.mozsearch-index.zip",
        ),
        status=404,
    )

    assert build_platforms.retrieve_built_files() == {
        "linux": ["linux64-searchfox-debug/browser.js", "widget/gtk/nsWindow.cpp"],
        "macosx": [
            "dom/base/SSE2.cpp",
            "macosx64-aarch64-searchfox-debug/browser.js",
            "macosx64-searchfox-debug/browser.js",
            "widget/cocoa/nsCocoaWindow.mm",
        ],
        "windows": ["widget/windows/nsWindow.cpp", "win64-searchfox-debug/browser.js"],
    }
