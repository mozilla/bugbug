import pytest
from hackbot_agents.autowebcompat_etp.result import (
    PNG_SIGNATURE,
    EtpInvestigationResult,
    ProposedException,
    ProposedShim,
)
from pydantic import ValidationError

PROPOSAL = {
    "category": "convenience",
    "urlPattern": "*://platform.linkedin.com/*",
    "topLevelUrlPattern": "*://vasscompany.csod.com/*",
    "classifierFeatures": ["socialtracking-protection", "tracking-protection"],
    "rationale": "Loads the Apply With LinkedIn widget.",
}


def make_result(**overrides):
    fields = {
        "summary": "The LinkedIn button is missing in Strict mode.",
        "failure_reason": None,
        "broken_in_standard": False,
        "broken_in_strict": True,
        "broken_with_easyprivacy": False,
        "broken_with_easylist": True,
        "culprit_prefs": ["privacy.trackingprotection.socialtracking.enabled"],
        "blocked_resources": ["https://platform.linkedin.com (socialtracking)"],
        "proposed_exceptions": [PROPOSAL],
        "exception_verified": True,
        "severity": "S3",
        "priority": "P3",
        "steps": "1. Set browser.contentblocking.category to strict.",
    }
    return EtpInvestigationResult.model_validate(fields | overrides)


@pytest.mark.parametrize(
    "pattern",
    ["*://*.licdn.com/*", "https://cdn.example.com/widget/*", "*://a.b:8443/*"],
)
def test_accepts_match_patterns(pattern):
    ProposedException.model_validate(PROPOSAL | {"urlPattern": pattern})


@pytest.mark.parametrize(
    "pattern", ["*://*/*", "platform.linkedin.com", "ftp://example.com/*"]
)
def test_rejects_bad_match_patterns(pattern):
    with pytest.raises(ValidationError):
        ProposedException.model_validate(PROPOSAL | {"urlPattern": pattern})


def test_global_exception_has_no_top_level_pattern():
    proposal = ProposedException.model_validate(PROPOSAL | {"topLevelUrlPattern": None})
    assert proposal.topLevelUrlPattern is None


def test_requires_a_feature():
    with pytest.raises(ValidationError):
        ProposedException.model_validate(PROPOSAL | {"classifierFeatures": []})


# Mirror existing entries in browser/extensions/webcompat/data/shims.js.
SHIM = {
    "kind": "opt_in_placeholder",
    "name": "Instagram embed placeholder",
    "matches": [
        "https://www.instagram.com/embed.js",
        "https://platform.instagram.com/*/embeds.js",
    ],
    "unblocksOnOptIn": [
        "*://www.instagram.com/*",
        "*://platform.instagram.com/*",
        "*://*.fbcdn.net/*",
    ],
    "behavior": "Show an Instagram placeholder; load the embed on click.",
    "existing_shim_id": "InstagramEmbed",
    "rationale": "Embedded posts are ancillary; the page works without them.",
}
STUB_SHIM = {
    "kind": "stub",
    "name": "AdNexus AST",
    "matches": ["*://*.adnxs.com/*/ast.js*"],
    "unblocksOnOptIn": [],
    "behavior": "Define a no-op apntag so the page's ad setup doesn't throw.",
    "existing_shim_id": "AdNexusAST",
    "rationale": "Only ads are lost; a stub keeps the page working.",
}


def test_success_needs_a_verified_proposal():
    make_result()
    with pytest.raises(ValidationError, match="either exceptions or a shim"):
        make_result(proposed_exceptions=[])
    with pytest.raises(ValidationError, match="exception_not_verified"):
        make_result(exception_verified=False)


def test_success_with_a_shim_instead_of_exceptions():
    result = make_result(proposed_exceptions=[], proposed_shim=SHIM)
    assert result.proposed_shim.kind == "opt_in_placeholder"
    with pytest.raises(ValidationError, match="not both"):
        make_result(proposed_shim=SHIM)


def test_failure_has_no_proposals():
    make_result(failure_reason="not_reproducible", proposed_exceptions=[])
    with pytest.raises(ValidationError, match="must be empty"):
        make_result(failure_reason="not_fixable_by_exception")
    with pytest.raises(ValidationError, match="must be empty"):
        make_result(failure_reason="other", proposed_exceptions=[], proposed_shim=SHIM)


def test_anti_adblock_needs_verified_exceptions():
    make_result(failure_reason="anti_adblock")
    make_result(failure_reason="anti_adblock", proposed_shim=STUB_SHIM)
    make_result(failure_reason="anti_adblock", proposed_shim=SHIM)
    with pytest.raises(ValidationError, match="needs proposed_exceptions"):
        make_result(
            failure_reason="anti_adblock", proposed_exceptions=[], proposed_shim=SHIM
        )
    with pytest.raises(ValidationError, match="requires exception_verified"):
        make_result(failure_reason="anti_adblock", exception_verified=False)


def test_filter_lists_only_when_strict_is_broken():
    make_result(broken_with_easyprivacy=True)
    make_result(
        failure_reason="not_reproducible",
        broken_in_strict=False,
        broken_with_easyprivacy=None,
        broken_with_easylist=None,
        proposed_exceptions=[],
    )
    with pytest.raises(ValidationError, match="broken_with_easyprivacy"):
        make_result(
            failure_reason="not_reproducible",
            broken_in_strict=False,
            broken_with_easylist=None,
            proposed_exceptions=[],
        )
    with pytest.raises(ValidationError, match="broken_with_easylist"):
        make_result(
            failure_reason="not_reproducible",
            broken_in_strict=False,
            broken_with_easyprivacy=None,
            proposed_exceptions=[],
        )


def test_shim_unblocks_match_kind():
    with pytest.raises(ValidationError, match="must not be empty"):
        ProposedShim.model_validate(SHIM | {"unblocksOnOptIn": []})
    ProposedShim.model_validate(STUB_SHIM)
    with pytest.raises(ValidationError, match="must be empty for a stub"):
        ProposedShim.model_validate(
            STUB_SHIM | {"unblocksOnOptIn": ["*://*.adnxs.com/*"]}
        )


def test_shim_patterns_are_validated():
    with pytest.raises(ValidationError):
        ProposedShim.model_validate(SHIM | {"matches": ["instagram.com"]})


def test_screenshots_must_be_pngs(tmp_path):
    png = tmp_path / "strict.png"
    png.write_bytes(PNG_SIGNATURE + b"rest")
    assert make_result(screenshot_strict=png).screenshot_strict == png

    empty = tmp_path / "empty.png"
    empty.touch()
    with pytest.raises(ValidationError, match="not a valid PNG"):
        make_result(screenshot_strict=empty)
    with pytest.raises(ValidationError, match="Cannot read"):
        make_result(screenshot_fixed=tmp_path / "missing.png")
    with pytest.raises(ValidationError, match="not a valid PNG"):
        make_result(screenshot_easyprivacy=empty)
    with pytest.raises(ValidationError, match="not a valid PNG"):
        make_result(screenshot_easylist=empty)


def test_blocked_trackers_only_for_taken_screenshots(tmp_path):
    png = tmp_path / "strict.png"
    png.write_bytes(PNG_SIGNATURE)
    result = make_result(screenshot_strict=png, blocked_trackers={"strict": 7})
    assert result.blocked_trackers.strict == 7
    assert make_result().blocked_trackers.strict is None

    with pytest.raises(ValidationError, match="screenshot_fixed is null"):
        make_result(blocked_trackers={"fixed": 0})
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        make_result(screenshot_strict=png, blocked_trackers={"strict": -1})
