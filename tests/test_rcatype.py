# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.rcatype import RCATypeModel


def test_get_rca_from_whiteboard():
    model = RCATypeModel()
    # Case 1: No rca
    assert model.get_rca_from_whiteboard("[Whiteboard1][Not RCA type]") == []
    # Case 2: RCA : A and RCA - A
    assert model.get_rca_from_whiteboard("[RCA: cornercase]") == ["cornercase"]
    assert model.get_rca_from_whiteboard("[rca - codingerror]") == ["codingerror"]
    # Case 3: Multiple rca types
    assert model.get_rca_from_whiteboard("[rca - cornercase][rca - codingerror]") == [
        "cornercase",
        "codingerror",
    ]
    assert model.get_rca_from_whiteboard("[rca : systemerror][rca - codingerror]") == [
        "systemerror",
        "codingerror",
    ]
    assert model.get_rca_from_whiteboard("[rca - cornercase][rca : testingerror]") == [
        "cornercase",
        "testingerror",
    ]
    assert model.get_rca_from_whiteboard("[rca : cornercase][rca : codingerror]") == [
        "cornercase",
        "codingerror",
    ]
    assert model.get_rca_from_whiteboard("[RCA: codingerror - syntaxerror]") == [
        "codingerror"
    ]
    # Case 4: subcategories enabled, with rca already present in the list
    model = RCATypeModel(rca_subcategories_enabled=True)
    assert model.get_rca_from_whiteboard("[RCA: codingerror - syntaxerror]") == [
        "codingerror-syntaxerror"
    ]
    assert model.get_rca_from_whiteboard(
        "[RCA: codingerror - syntaxerror][rca: codingerror:logicalerror]"
    ) == ["codingerror-syntaxerror", "codingerror-logicalerror"]
    # Case 5: subcategories enabled, with rca not present in list
    assert model.get_rca_from_whiteboard("[RCA: codingerror - semanticerror]") == [
        "codingerror-semanticerror"
    ]


def test_get_labels():
    model = RCATypeModel()
    classes, _ = model.get_labels()

    assert classes[1556846].tolist() == [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]
