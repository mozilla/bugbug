"""Tests for the team file and the Bugzilla terms file the agent reads at import."""

import copy
from pathlib import Path

import pytest
from hackbot_agents.gecko_triage.config import (
    ISSUE_TYPES,
    PEOPLE,
    TEAM,
    TEAM_META_BUGS,
    TEAMS_DIR,
    TERMS,
)
from hackbot_agents.gecko_triage.teamconfig import (
    People,
    Team,
    Terms,
    meta_bug_ids,
    team_dir,
)
from pydantic import ValidationError

TEAM_DATA = {
    "label": "Firefox Audio/Video",
    "comment": {"capture_marker": "about:logging", "capture_block": "Steps."},
    "components": [
        {"product": "Core", "component": "Audio/Video", "trees": ["dom/media/"]},
    ],
}

PEOPLE_DATA = {
    "employee_domain": "@Example.com",
    "qa_name_markers": ["QA"],
    "qa_accounts": ["Tester@Example.com"],
}

TERMS_DATA = {
    "history": {"no_effect": ["cc"], "no_effect_prefixes": ["cf_status_"]},
    "meta_summary": r"\[meta\]",
    "keywords": {"meta": "meta", "add": ["stalled", "regression"]},
    "fields": {"qa_whiteboard": "cf_qa_whiteboard"},
    "whiteboard": {
        "skip": "[gecko-triage:skip]",
        "easy_fix": "[gecko-triage:easy-fix]",
        "gfb_candidate": "[gecko-triage:gfb-candidate]",
    },
    "no_team": {"products": ["Core"]},
}


def team(**changes):
    data = copy.deepcopy(TEAM_DATA)
    for path, value in changes.items():
        *parents, leaf = path.split("__")
        node = data
        for key in parents:
            node = node[key]
        node[leaf] = value
    return data


def test_a_team_dir_parses():
    parsed = Team.model_validate(TEAM_DATA)
    assert parsed.components[0].key == "Core :: Audio/Video"
    assert parsed.components[0].trees == ("dom/media/",)


def test_logins_and_the_domain_are_lower_cased():
    people = People.model_validate(PEOPLE_DATA)
    assert people.qa_accounts == frozenset({"tester@example.com"})
    assert people.employee_domain == "@example.com"


def test_people_refuse_a_typo():
    with pytest.raises(ValidationError):
        People.model_validate(dict(PEOPLE_DATA, qa_acounts=[]))


@pytest.mark.parametrize(
    "changes",
    [
        {"components": []},
        {"comment": {"capture_marker": "about:logging"}},
        {"colour": "blue"},
        {"components": [{"product": "Core", "component": "X", "tree": ["a/"]}]},
        {"comment__capture_blok": "x"},
        {
            "comment": {
                "capture_marker": "about:logging",
                "capture_block": "Steps.",
                "capture_labels": ["Start Logging"],
            }
        },
        {"label": 24},
        {"components": [{"product": "Core", "component": "X", "trees": "dom/"}]},
    ],
    ids=[
        "no components",
        "missing key",
        "unknown key",
        "component typo",
        "nested typo",
        "label not in the block",
        "int for string",
        "string for list",
    ],
)
def test_a_bad_team_dir_is_refused(changes):
    with pytest.raises(ValidationError):
        Team.model_validate(team(**changes))


EXPERT_DATA = {
    "email": " Expert@Example.org ",
    "areas": ["Linux hardware video decoding", "VA-API"],
    "when": "The bug is on Linux.",
}


def test_a_team_without_experts_has_empty_lists():
    parsed = Team.model_validate(TEAM_DATA)
    assert parsed.needinfo_experts == () and parsed.needinfo_fallback == ()
    assert Team.model_validate(team(needinfo_experts=[])).needinfo_experts == ()


def test_an_expert_parses_with_a_normalized_email_and_several_areas():
    parsed = Team.model_validate(team(needinfo_experts=[EXPERT_DATA]))
    (expert,) = parsed.needinfo_experts
    assert expert.email == "expert@example.org"
    assert expert.areas == ("Linux hardware video decoding", "VA-API")
    assert expert.when == "The bug is on Linux."


def test_an_expert_with_no_condition_parses():
    data = {k: v for k, v in EXPERT_DATA.items() if k != "when"}
    (expert,) = Team.model_validate(team(needinfo_experts=[data])).needinfo_experts
    assert expert.when is None


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "a@example.com, b@example.com"},
        {"email": "expert"},
        {"email": "a@example"},
        {"email": 7},
        {"areas": []},
        {"areas": [""]},
        {"areas": "Web Audio"},
        {"areas": ["Web Audio", "web audio "]},
        {"when": " "},
        {"area": "x"},
    ],
    ids=[
        "two emails",
        "no domain",
        "no tld",
        "int",
        "no area",
        "empty area",
        "string for list",
        "area twice",
        "blank when",
        "old key",
    ],
)
def test_a_bad_expert_is_refused(changes):
    with pytest.raises(ValidationError):
        Team.model_validate(team(needinfo_experts=[dict(EXPERT_DATA, **changes)]))


def test_the_same_expert_email_twice_is_refused():
    other = dict(EXPERT_DATA, areas=["Another area"], email="EXPERT@example.org")
    with pytest.raises(ValidationError, match="expert@example.org"):
        Team.model_validate(team(needinfo_experts=[EXPERT_DATA, other]))


@pytest.mark.parametrize("missing", ["areas", "email"])
def test_an_expert_needs_an_email_and_areas(missing):
    data = {k: v for k, v in EXPERT_DATA.items() if k != missing}
    with pytest.raises(ValidationError):
        Team.model_validate(team(needinfo_experts=[data]))


def test_the_fallback_list_parses_with_normalized_emails():
    parsed = Team.model_validate(
        team(needinfo_fallback=[" A@Example.com", "b@example.com"])
    )
    assert parsed.needinfo_fallback == ("a@example.com", "b@example.com")


@pytest.mark.parametrize(
    "fallback",
    [["a@example.com", "A@example.com "], ["not-an-email"], "a@example.com"],
    ids=["twice", "bad email", "string for list"],
)
def test_a_bad_fallback_list_is_refused(fallback):
    with pytest.raises(ValidationError):
        Team.model_validate(team(needinfo_fallback=fallback))


def test_a_team_without_issue_types_has_an_empty_list():
    assert Team.model_validate(team()).issue_types == ()


def test_issue_types_parse_in_order():
    parsed = Team.model_validate(
        team(
            issue_types=[
                {"value": "av-sync", "meaning": "audio and video out of sync"},
                {"value": "drm2", "meaning": " EME "},
            ]
        )
    )
    assert [(t.value, t.meaning) for t in parsed.issue_types] == [
        ("av-sync", "audio and video out of sync"),
        ("drm2", "EME"),
    ]


@pytest.mark.parametrize(
    "entry",
    [
        {"value": "Playback", "meaning": "m"},
        {"value": "stall freeze", "meaning": "m"},
        {"value": "stall_freeze", "meaning": "m"},
        {"value": "", "meaning": "m"},
        {"value": "other", "meaning": "m"},
        {"value": "drm", "meaning": " "},
        {"value": "drm"},
        {"meaning": "m"},
        {"value": "drm", "meaning": "m", "extra": 1},
    ],
    ids=[
        "upper case",
        "space",
        "underscore",
        "empty",
        "other",
        "blank meaning",
        "no meaning",
        "no value",
        "unknown key",
    ],
)
def test_a_bad_issue_type_is_refused(entry):
    with pytest.raises(ValidationError):
        Team.model_validate(team(issue_types=[entry]))


def test_the_same_issue_type_twice_is_refused():
    entry = {"value": "drm", "meaning": "m"}
    with pytest.raises(ValidationError, match="drm"):
        Team.model_validate(team(issue_types=[entry, dict(entry, meaning="n")]))


MEDIA_ISSUE_TYPES = [
    ("playback-failure", "does not start, error, unsupported format"),
    ("stall-freeze", "plays, then stops, freezes or skips"),
    ("av-sync", "audio and video out of sync"),
    ("audio-output", "no sound, glitches, crackling, wrong device"),
    ("video-rendering", "corruption, wrong colors, green frames, scaling"),
    ("performance", "high CPU or memory, dropped frames"),
    (
        "codec-support",
        "hardware or software decoder or encoder availability, MediaCapabilities",
    ),
    ("drm", "EME, Widevine, PlayReady, GMP"),
    ("capture", "getUserMedia, MediaRecorder, camera, microphone"),
    ("web-api", "Web Audio, Web Codecs, media element spec behavior"),
]


def test_the_media_team_lists_its_issue_types_in_order():
    assert [(t.value, t.meaning) for t in TEAM.issue_types] == MEDIA_ISSUE_TYPES
    assert {*(v for v, _ in MEDIA_ISSUE_TYPES), "other"} == ISSUE_TYPES


MEDIA_EXPERTS = {
    "padenot@mozilla.com": [
        "Web Audio",
        "WebCodecs",
        "Web Speech",
        "audio-specific issues",
    ],
    "karlt@mozbugz.karlt.net": ["Web Audio", "MediaStreamGraph (MTG)"],
    "kinetik@flim.org": ["cubeb", "AudioIPC"],
    "cchang@mozilla.com": ["WebCodecs"],
    "aosmond@mozilla.com": ["GMP", "Android NDK", "graphics in media"],
    "jolin@mozilla.com": ["Android", "HLS"],
    "azebrowski@mozilla.com": ["Media Capabilities", "codec support"],
    "alwu@mozilla.com": ["autoplay", "WebVTT", "PlayReady"],
}


def test_the_media_team_lists_its_experts_by_person():
    experts = {e.email: e for e in TEAM.needinfo_experts}
    for email, areas in MEDIA_EXPERTS.items():
        assert list(experts[email].areas) == areas, email
    assert set(experts) == {
        *MEDIA_EXPERTS,
        "stransky@anakreon.cz",
        "tboiko@nvidia.com",
    }


def test_the_media_team_lists_its_linux_hardware_decode_expert():
    experts = {e.email: e for e in TEAM.needinfo_experts}
    assert "stransky@redhat.com" not in experts
    expert = experts["stransky@anakreon.cz"]
    assert any("Linux" in a and "VA-API" in a for a in expert.areas)
    assert "HARDWARE_VIDEO_DECODING" in expert.when
    assert "SWDEC" in expert.when
    assert "blocklisted, or when Codec Support lists only SWDEC" in expert.when
    # NVIDIA and Vulkan bugs go to the NVIDIA expert instead.
    assert not any("Vulkan" in a for a in expert.areas)
    assert "any GPU in about:support is NVIDIA" in expert.when
    assert "ask tboiko@nvidia.com" in expert.when


def test_the_media_team_lists_its_nvidia_and_vulkan_expert():
    expert = {e.email: e for e in TEAM.needinfo_experts}["tboiko@nvidia.com"]
    assert list(expert.areas) == ["NVIDIA GPUs on Linux", "Vulkan video decoding"]
    assert "any GPU in about:support is NVIDIA" in expert.when
    assert "Vulkan" in expert.when


def test_the_media_team_has_its_fallback_list():
    assert TEAM.needinfo_fallback == (
        "alwu@mozilla.com",
        "padenot@mozilla.com",
        "jmathies@mozilla.com",
    )


def test_a_terms_file_parses():
    terms = Terms.model_validate(TERMS_DATA)
    assert terms.keywords.add == ("stalled", "regression")
    assert terms.fields.qa_whiteboard == "cf_qa_whiteboard"
    assert terms.meta_summary.search("[META] AV1")
    assert terms.no_team.products == ("Core",)
    assert not terms.history.affects_analysis("cc")
    assert not terms.history.affects_analysis("cf_status_firefox150")
    assert terms.history.affects_analysis("priority")
    assert terms.whiteboard.fix_tags == (
        "[gecko-triage:easy-fix]",
        "[gecko-triage:gfb-candidate]",
    )


@pytest.mark.parametrize(
    "whiteboard",
    [
        {"skip": "[s]", "easy_fix": "[e]"},
        {"skip": "[s]", "easy_fix": "[e]", "gfb_candidate": " "},
        {"skip": "[s]", "easy_fix": "[e]", "gfb_candidate": "[e]"},
        {"skip": "[s]", "easy_fix": "[s]", "gfb_candidate": "[g]"},
    ],
)
def test_the_fix_tags_fail_closed(whiteboard):
    with pytest.raises(ValidationError):
        Terms.model_validate({**TERMS_DATA, "whiteboard": whiteboard})


@pytest.mark.parametrize("products", [[], [""], ["Core", 1]])
def test_the_no_team_products_fail_closed(products):
    with pytest.raises(ValidationError):
        Terms.model_validate({**TERMS_DATA, "no_team": {"products": products}})


def test_the_capture_marker_is_lower_cased():
    data = team(comment={"capture_marker": "About:Logging", "capture_block": "Steps."})
    assert Team.model_validate(data).comment.capture_marker == "about:logging"


def test_terms_need_the_meta_keyword():
    data = dict(TERMS_DATA, keywords={"add": ["stalled"]})
    with pytest.raises(ValidationError):
        Terms.model_validate(data)


def test_the_shipped_files_load():
    assert len(TEAM.components) == 8
    assert "about:logging" in TEAM.comment.capture_block
    assert TERMS.keywords.meta == "meta"
    assert len(PEOPLE.qa_accounts) == 1


@pytest.mark.parametrize("name", ["../media", "media/../x", "Media", "", "nope"])
def test_a_bad_or_unknown_team_name_is_refused(name):
    with pytest.raises(ValueError):
        team_dir(TEAMS_DIR, name)


def test_a_known_team_name_resolves():
    assert team_dir(TEAMS_DIR, "media") == TEAMS_DIR / "media"


def test_meta_bug_ids_reads_the_table_rows():
    text = "| Alias | Bug | Area |\n| --- | --- | --- |\n| x | 1416090 | a |\n|  | 2026773 | b |\nSee bug 999999.\n"
    assert meta_bug_ids(text) == {1416090, 2026773}


def test_the_media_team_has_its_meta_bugs():
    assert 1416090 in TEAM_META_BUGS


def test_a_table_row_without_a_bug_id_is_refused():
    text = "| Alias | Bug | Area |\n| --- | --- | --- |\n| x | 14160x | a |\n"
    with pytest.raises(ValueError, match="14160x"):
        meta_bug_ids(text)


def test_bot_aliases_come_from_a_comma_separated_list():
    from hackbot_agents.gecko_triage.config import parse_bot_aliases

    assert parse_bot_aliases("") == frozenset()
    assert parse_bot_aliases(" A@Example.com, b@example.com ,") == frozenset(
        {"a@example.com", "b@example.com"}
    )
    with pytest.raises(RuntimeError):
        parse_bot_aliases("a@example.com, not-an-email")


def test_no_bot_alias_is_in_the_repo():
    from hackbot_agents.gecko_triage.config import PEOPLE

    assert not hasattr(PEOPLE, "bot_aliases")


def _classify_rows():
    text = (TEAMS_DIR / "media" / "rules" / "classify.md").read_text()
    rows = [line for line in text.splitlines() if line.startswith("| ")][2:]
    return {
        cells[0]: cells
        for cells in ([c.strip() for c in row.strip("|").split("|")] for row in rows)
    }


def test_every_media_classify_component_is_a_team_component():
    names = {c.component for c in TEAM.components}
    for row_type, (_, _, components) in _classify_rows().items():
        for name in components.split(", "):
            assert name in names or f"Audio/Video: {name}" in names, (row_type, name)


def test_playready_and_autoplay_belong_to_playback():
    rows = _classify_rows()
    assert "PlayReady" in rows["PlayReady"][1]
    assert rows["PlayReady"][2] == "Audio/Video: Playback"
    assert "PlayReady" not in rows["GMP / EME"][1]
    assert rows["Autoplay"][2] == "Audio/Video: Playback"
    notes = {c.component: c.notes for c in TEAM.components}
    assert "PlayReady" not in notes["Audio/Video: GMP"]
    assert "PlayReady" in notes["Audio/Video: Playback"]
    assert "autoplay" in notes["Audio/Video: Playback"]


def test_the_wmf_platform_code_belongs_to_playback():
    trees = {c.component: c.trees for c in TEAM.components}
    assert "dom/media/platforms/wmf/" in trees["Audio/Video: Playback"]
    assert "dom/media/platforms/wmf/" not in trees["Audio/Video: GMP"]


REPO = Path(__file__).parents[3]
OPTIONAL_TEAM = (
    "Triage a Gecko bug. A team, such as media, adds its own rules. With no team, the "
    "run uses the shared rules for the bug's component."
)


@pytest.mark.parametrize(
    "path",
    [
        "services/hackbot-api/app/agents.py",
        "docs/hackbot/agents.md",
        "agents/gecko-triage/README.md",
        "agents/gecko-triage/hackbot_agents/gecko_triage/agent.py",
    ],
)
def test_every_description_reads_the_team_as_optional(path):
    text = (REPO / path).read_text()
    flat = " ".join(text.replace('"\n', "").replace('            "', "").split())
    for phrase in ("for one team", "one team per run", "Audio/Video first"):
        assert phrase not in flat, phrase
    assert "A team, such as media, adds its own rules." in flat
