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
