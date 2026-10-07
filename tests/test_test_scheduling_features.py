# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug import test_scheduling_features


def test_path_distance():
    pd = test_scheduling_features.PathDistance()

    assert (
        pd(
            {"name": "dom/media/tests/mochitest.ini"},
            {"files": ["dom/media/tests/test.js", "dom/media/anotherFile.cpp"]},
        )
        == 0
    )
    assert (
        pd(
            {"name": "dom/media/tests/mochitest.ini"},
            {"files": ["dom/media/anotherFile.cpp"]},
        )
        == 1
    )
    assert (
        pd(
            {"name": "dom/media/tests/mochitest.ini"},
            {"files": ["dom/media/src/aFile.cpp"]},
        )
        == 2
    )
    assert (
        pd(
            {"name": "dom/media/tests/mochitest.ini"},
            {"files": ["dom/media/src/aFile.cpp", "dom/media/anotherFile.cpp"]},
        )
        == 1
    )
    assert (
        pd(
            {"name": "dom/media/tests/mochitest.ini"},
            {"files": ["layout/utils/bla.cpp"]},
        )
        == 5
    )
    assert (
        pd(
            {"name": "testing/web-platform/tests/content-security-policy/worker-src"},
            {"files": ["test"]},
        )
        == 4
    )
    assert (
        pd(
            {"name": "test"},
            {
                "files": [
                    "testing/web-platform/tests/content-security-policy/worker-src"
                ]
            },
        )
        == 4
    )


def test_manifest_suite_classification():
    suite = test_scheduling_features.ManifestSuite()
    assert (
        suite({"name": "testing/web-platform/tests/css/css-grid"}, commit={}) == "WPT"
    )
    assert (
        suite({"name": "testing/web-platform/mozilla/tests/webgpu"}, commit={}) == "WPT"
    )
    assert (
        suite({"name": "dom/base/crashtests/crashtests.list"}, commit={}) == "crashtest"
    )
    assert (
        suite({"name": "layout/reftests/reftest-sanity/scripttests.list"}, commit={})
        == "reftest"
    )
    assert (
        suite(
            {"name": "devtools/client/webconsole/test/browser/_webconsole.toml"},
            commit={},
        )
        == "mochitest"
    )
    assert (
        suite(
            {
                "name": "toolkit/components/extensions/test/xpcshell/native_messaging.toml"
            },
            commit={},
        )
        == "xpcshell"
    )
    assert suite({"name": "js/src/tests/non262/Array/jstests.list"}, commit={}) == (
        "jstest"
    )
    assert suite({"name": "gfx/tests/something.toml"}, commit={}) is None


def test_touched_group_dirs():
    touched = test_scheduling_features.TouchedGroupDirs()
    commit = {
        "files": [
            "dom/base/test/test_foo.html",
            "dom/base/nsDocument.cpp",
            "testing/web-platform/meta/css/css-grid/grid-1.html.ini",
        ]
    }
    assert touched({"name": "dom/base/test/mochitest.toml"}, commit) == {
        "touch_group_dirs": 1,
        "touch_parent_dir": 2,
    }
    assert touched({"name": "testing/web-platform/tests/css/css-grid"}, commit) == {
        "touch_group_dirs": 1,
        "touch_parent_dir": 0,
    }


def test_task_name_tokens():
    tokens = test_scheduling_features.TaskNameTokens()
    assert tokens({"name": "test-linux2404-64/debug-gtest-1proc"}) == [
        "test",
        "linux2404",
        "debug",
        "gtest",
        "1proc",
    ]
    assert tokens({"name": "build-win64-asan/opt"}) == ["build", "win64", "asan", "opt"]
    # Chunk numbers are dropped.
    assert tokens(
        {"name": "test-windows11-64-25h2/debug-telemetry-tests-client-2"}
    ) == [
        "test",
        "windows11",
        "25h2",
        "debug",
        "telemetry",
        "tests",
        "client",
    ]
    assert tokens({"name": "build-apk-focus-debug"}) == [
        "build",
        "apk",
        "focus",
        "debug",
    ]


PUSH_FILES_COMMIT = {
    "files": [
        "mobile/android/geckoview/src/main/java/Foo.java",
        "widget/android/nsWindow.cpp",
        "build/moz.configure/toolchain.configure",
        "dom/base/Document.cpp",
        "Cargo.lock",
        "dom/base/test/mochitest.toml",
        "accessible/mac/MOXAccessibleBase.mm",
    ]
}


def test_push_build_files():
    extractor = test_scheduling_features.PushBuildFiles()
    features = extractor({"name": "build-win64/opt"}, PUSH_FILES_COMMIT)
    assert features["push_build_files_type_build"] == 1
    assert features["push_build_files_type_deps"] == 1
    assert features["push_build_files_type_manifests"] == 1
    assert features["push_build_files_type_java"] == 1
    assert features["push_build_files_os_android"] == 2
    assert features["push_build_files_os_macosx"] == 1
    assert features["push_build_files_os_windows"] == 0
    # Only build tasks get them.
    assert (
        extractor({"name": "test-linux2404-64/opt-gtest-1proc"}, PUSH_FILES_COMMIT)
        is None
    )
    assert extractor({"name": "build-signing-win64/opt"}, PUSH_FILES_COMMIT) is None


def test_push_task_files():
    extractor = test_scheduling_features.PushTaskFiles()
    # The build system files are the build file (toolchain.configure) and Cargo.lock.
    assert extractor(
        {"name": "build-android-aarch64-fenix/debug"}, PUSH_FILES_COMMIT
    ) == {"push_platform_files": 2, "push_build_system_files": 2}
    assert extractor({"name": "build-macosx64/opt"}, PUSH_FILES_COMMIT) == {
        "push_platform_files": 1,
        "push_build_system_files": 2,
    }
    assert extractor(
        {"name": "test-android-em-14-x86_64/debug-geckoview-junit-nofis"},
        PUSH_FILES_COMMIT,
    ) == {
        "push_platform_files": 2,
        "push_test_platform_files": 2,
        "push_suite_files": 1,
    }
    gtest_commit = {
        "files": [
            "xpcom/tests/gtest/TestFoo.cpp",
            "security/sandbox/win/foo.cpp",
            "testing/gtest/rungtests.py",
        ]
    }
    # Without the test directories from the tree, only the harness files.
    assert extractor(
        {"name": "test-windows11-64-25h2/debug-gtest-1proc"}, gtest_commit
    ) == {
        "push_platform_files": 1,
        "push_test_platform_files": 1,
        "push_suite_files": 1,
    }
    extractor_with_dirs = test_scheduling_features.PushTaskFiles(
        {"gtest": ("xpcom/tests/gtest/",)}
    )
    assert (
        extractor_with_dirs(
            {"name": "test-windows11-64-25h2/debug-gtest-1proc"}, gtest_commit
        )["push_suite_files"]
        == 2
    )
    # Android components get the files of their own source directory.
    commit = {
        "files": [
            "mobile/android/android-components/components/feature/logins/src/Foo.kt",
            "mobile/android/android-components/components/feature/share/src/Bar.kt",
        ]
    }
    assert extractor({"name": "build-components-feature-logins"}, commit) == {
        "push_platform_files": 2,
        "push_source_files": 1,
        "push_build_system_files": 0,
    }
    assert extractor({"name": "test-components-android-feature-logins"}, commit) == {
        "push_platform_files": 2,
        "push_source_files": 1,
        "push_test_platform_files": 2,
        "push_suite_files": 2,
    }


def test_get_task_source_dir():
    get_task_source_dir = test_scheduling_features.get_task_source_dir
    assert (
        get_task_source_dir("build-components-browser-engine-gecko")
        == "mobile/android/android-components/components/browser/engine-gecko/"
    )
    assert (
        get_task_source_dir("test-components-android-feature-logins")
        == "mobile/android/android-components/components/feature/logins/"
    )
    assert (
        get_task_source_dir("build-samples-browser")
        == "mobile/android/android-components/samples/browser/"
    )
    assert get_task_source_dir("build-apk-fenix-debug") == "mobile/android/fenix/"
    assert get_task_source_dir("test-apk-focus-debug") == (
        "mobile/android/focus-android/"
    )
    assert get_task_source_dir("build-win64/opt") is None


def test_get_task_os():
    get_task_os = test_scheduling_features.get_task_os
    assert get_task_os("test-linux2404-64/debug-gtest-1proc") == "linux"
    assert get_task_os("build-win64-asan/opt") == "windows"
    assert get_task_os("build-signing-macosx64/opt") == "macosx"
    assert get_task_os("build-apk-focus-debug") == "android"
    assert get_task_os("test-android-em-14-x86_64/debug-geckoview-junit-nofis") == (
        "android"
    )
    assert get_task_os("build-ios-non-unified/plain") == "ios"
    assert get_task_os("build-unknown/opt") == "other"
