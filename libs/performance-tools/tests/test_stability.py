from __future__ import annotations

from dataclasses import asdict

from performance_tools.calculator.perf_triage import calculate_impact
from performance_tools.triage.stability import (
    args_from_selections,
    check_stability,
    emittable_keywords,
)

# The selections step 1 returned for bug 1839859 on 2026-10-21 (low, 7.2),
# copied from that run's summary.json.
SEL_1839859 = {
    "platforms": [
        "platform-windows",
        "platform-macos",
        "platform-linux",
        "platform-android",
    ],
    "browser": ["browser-minor"],
    "site": ["site-major"],
    "configuration": ["scenario-general"],
    "pageload": ["pageload-none"],
    "pages": ["pages-rare"],
    "resource": ["resource-none"],
    "reproducible": ["reproducible-reproduced"],
    "additional": [],
}


def test_selections_round_trip():
    score, impact, _, selections = calculate_impact(
        **asdict(args_from_selections(SEL_1839859))
    )
    assert selections == SEL_1839859
    assert (round(score, 2), impact) == (7.2, "low")


def test_nothing_unestablished_is_confident():
    s = check_stability(SEL_1839859, [])
    assert s.confident
    assert s.bands == {"low"}
    assert s.flipping == ()


def test_pages_affected_flips_1839859():
    # rare (x0.3) -> major (x5) takes 7.2 to 120: low -> high.
    s = check_stability(SEL_1839859, ["pages-affected"])
    assert not s.confident
    assert "pages-affected" in s.flipping
    assert "high" in s.bands


def test_an_input_that_cannot_cross_a_band_stays_confident():
    # All four platforms already hit the group cap; varying to "all four" is a no-op.
    s = check_stability(SEL_1839859, ["platforms"])
    assert s.confident
    assert s.flipping == ()


def test_unknown_input_holds():
    s = check_stability(SEL_1839859, ["reproduceable"])
    assert not s.confident
    assert s.unknown == ("reproduceable",)


def test_boolean_factor_toggles():
    s = check_stability(SEL_1839859, ["chrome", "animation"])
    # chrome on: 7.2 * 0.3 = 2.16; animation on: (12 + 1) * 0.6 = 7.8; both low.
    assert s.confident
    assert s.bands == {"low"}


def test_boolean_factor_can_flip():
    # multiple-sites doubles: 14.4 -> medium.
    s = check_stability(SEL_1839859, ["multiple-sites"])
    assert not s.confident
    assert s.flipping == ("multiple-sites",)


def test_emittable_keywords():
    assert {"perf:responsiveness", "reproducible", "top50"} <= emittable_keywords()
    assert "perf" not in emittable_keywords()


def test_input_labels_come_from_the_calculator():
    from performance_tools.triage.stability import input_label, value_label

    assert input_label("pages-affected") == "Websites affected"
    assert input_label("reproducible") == "Reproducibility"
    assert input_label("platforms") == "Platforms"
    assert input_label("chrome") == "Reproduces in Chrome"
    assert input_label("reproduceable") == "reproduceable"
    assert value_label("browser-impact", "jank") == "Causes noticeable jank"
    assert value_label("animation", True) == "yes"
    assert value_label("nonsense", "x") == "x"


def test_calculator_args_defaults_match_the_calculator():
    # A drifted default would silently change what the stability gate scores.
    import inspect

    from performance_tools.triage.stability import CalculatorArgs

    params = inspect.signature(calculate_impact).parameters
    ours = CalculatorArgs()
    assert params["reproducible"].default == ours.reproducible
    for name, default in [
        ("browser_impact", "minor"),
        ("site_impact", "minor"),
        ("configuration", "general"),
        ("pageload_impact", "none"),
        ("pages_affected", "common"),
        ("resource_impact", "none"),
    ]:
        assert getattr(ours, name) == default
    assert args_from_selections({}) == ours


def test_unattempted_reproducibility_is_not_varied():
    # Unset on nearly every new bug; varying it to x0.5/x2 held every live run.
    sel = {**SEL_1839859, "reproducible": ["reproducible-not-attempted"]}
    s = check_stability(sel, ["reproducible"])
    assert s.confident and s.flipping == ()


def test_established_reproducible_is_not_varied():
    # reproduced (x2) vs. not-attempted (x1) flips this fixture from medium to
    # low, but reproducible is out of scope for this phase: only an
    # acknowledged-unestablished reproducible is varied.
    sel = {
        **SEL_1839859,
        "platforms": ["platform-windows"],
        "pages": ["pages-common"],
    }
    assert "reproducible" not in check_stability(sel, []).flipping
    assert "reproducible" in check_stability(sel, ["reproducible"]).flipping


def test_always_vary_platforms_matches_the_acknowledged_check():
    # platforms has no ladder, so ALWAYS_VARY falls back to the same
    # chosen-set-vs-all-four check an acknowledged-unestablished platforms gets.
    sel = {**SEL_1839859, "platforms": ["platform-linux"], "pages": ["pages-common"]}
    always_vary = check_stability(sel, [])
    acknowledged = check_stability(sel, ["platforms"])
    assert always_vary.bands == acknowledged.bands


def test_pages_affected_is_not_always_varied():
    # The one field excluded from ALWAYS_VARY -- see the module docstring for
    # why (its multiplier range makes any always-vary check too blunt). Confirms
    # an established-but-wrong pages-affected is NOT re-tested unless the skill
    # itself names it unestablished.
    sel = {**SEL_1839859, "pages": ["pages-rare"]}
    s = check_stability(sel, [])
    assert s.confident
    assert "pages-affected" not in s.flipping
