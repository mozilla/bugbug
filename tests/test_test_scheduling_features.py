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
