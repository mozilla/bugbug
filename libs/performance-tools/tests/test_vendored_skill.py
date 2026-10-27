from __future__ import annotations

from pathlib import Path

import performance_tools

SKILL = Path(performance_tools.__file__).parent / "skills" / "profiler-analysis"


def test_the_skill_is_vendored_with_its_references():
    """SKILL.md alone is not enough.

    The skill Globs references/ for the case study matching the scenario. Vendoring
    only SKILL.md leaves it running WITHOUT calibration and failing silently, which is
    worse than not having it.
    """
    assert (SKILL / "SKILL.md").is_file()
    refs = list((SKILL / "references").glob("*.md"))
    assert len(refs) >= 20, f"expected the full case-study set, found {len(refs)}"
    assert any(f.name.endswith("-cli.md") for f in refs)


def test_the_pin_is_recorded():
    """A vendored copy with no provenance cannot be bumped safely."""
    pinned = (SKILL.parent / "PINNED.md").read_text()
    assert "b77a9b0fd0fa" in pinned
    assert "mozilla-central" in pinned or "firefox" in pinned


def test_skill_frontmatter_name_matches_what_we_enable():
    """ClaudeAgentOptions.skills matches on the SKILL.md `name` field, not the dir."""
    head = (SKILL / "SKILL.md").read_text()[:400]
    assert "name: profiler-analysis" in head
