# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from typing import Any

import pytest

from bugbug import bugzilla


def test_get_bugs():
    all_bugs = {int(bug["id"]) for bug in bugzilla.get_bugs(include_invalid=True)}
    legitimate_bugs = {int(bug["id"]) for bug in bugzilla.get_bugs()}

    assert 1541482 in all_bugs
    assert 1541482 not in legitimate_bugs

    assert 1559674 in all_bugs
    assert 1559674 not in legitimate_bugs

    assert 1549207 in all_bugs
    assert 1549207 not in legitimate_bugs

    assert 1572747 in all_bugs
    assert 1572747 in legitimate_bugs


def test_get_fixed_versions():
    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "mozilla81",
                "cf_tracking_firefox83": "blocking",
                "cf_status_firefox82": "fixed",
                "cf_status_firefox81": "unaffected",
            }
        )
        == [81, 82]
    )

    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "mozilla82",
                "cf_tracking_firefox82": "---",
                "cf_status_firefox82": "fixed",
                "cf_status_firefox83": "fixed",
            }
        )
        == [82, 83]
    )

    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "mozilla82",
            }
        )
        == [82]
    )

    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "82 Branch",
            }
        )
        == [82]
    )

    assert (
        bugzilla.get_fixed_versions(
            {
                "target_milestone": "Firefox 82",
            }
        )
        == [82]
    )


@pytest.fixture
def component_team_mapping():
    return {
        "Crypto": {
            "Core": {
                "all_components": False,
                "named_components": ["Security: PSM"],
                "prefixed_components": [],
            },
            "JSS": {"all_components": True},
            "NSS": {"all_components": True},
        },
        "GFX": {
            "Core": {
                "all_components": False,
                "named_components": [
                    "Canvas: 2D",
                    "ImageLib",
                    "Panning and Zooming",
                    "Web Painting",
                ],
                "prefixed_components": ["GFX", "Graphics"],
            }
        },
        "Javascript": {
            "Core": {
                "all_components": False,
                "named_components": ["js-ctypes"],
                "prefixed_components": ["Javascript"],
            }
        },
    }


def test_get_component_team_mapping(
    responses: Any, component_team_mapping: dict
) -> None:
    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/config/component_teams",
        status=200,
        json=component_team_mapping,
    )

    assert bugzilla.get_component_team_mapping() == component_team_mapping


def test_component_to_team(component_team_mapping: dict) -> None:
    assert (
        bugzilla.component_to_team(component_team_mapping, "Core", "Security: PSM")
        == "Crypto"
    )
    assert (
        bugzilla.component_to_team(
            component_team_mapping, "JSS", "any component you want!"
        )
        == "Crypto"
    )
    assert (
        bugzilla.component_to_team(component_team_mapping, "Core", "Canvas: 2D")
        == "GFX"
    )
    assert (
        bugzilla.component_to_team(component_team_mapping, "Core", "ImageLib") == "GFX"
    )
    assert (
        bugzilla.component_to_team(component_team_mapping, "Core", "ImageLib2") is None
    )
    assert (
        bugzilla.component_to_team(
            component_team_mapping, "Core", "GFXsomethingsomething"
        )
        == "GFX"
    )
    assert (
        bugzilla.component_to_team(component_team_mapping, "Core", "Graphics: OK")
        == "GFX"
    )

    assert (
        bugzilla.component_to_team(component_team_mapping, "Core", "JavaScript Engine")
        == "Javascript"
    )
