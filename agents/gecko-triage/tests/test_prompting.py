"""Tests for the prompt templates and their renderer."""

import re

import jinja2
import pytest
from hackbot_agents.gecko_triage import prompting


def test_a_missing_value_fails_the_render():
    with pytest.raises(jinja2.UndefinedError):
        prompting.render("close_verifier")


def test_the_code_filter_wraps_a_value_in_backticks():
    template = prompting._ENV.from_string('{{ names | map("code") | join(", ") }}')
    assert template.render(names=["a", "b"]) == "`a`, `b`"


def test_every_prompt_file_is_a_template_that_parses():
    names = sorted(p.name for p in prompting.PROMPTS_DIR.glob("*.j2"))
    assert {"system.md.j2", "user.md.j2", "descriptions.j2"} <= set(names)
    for name in names:
        prompting._ENV.get_template(name)


# A `{name}` placeholder of `str.format`, which Jinja passes through as text.
_FORMAT_PLACEHOLDER = re.compile(r"(?<!\{)\{[a-z_]+\}(?!\})")


def test_the_placeholder_check_finds_a_format_placeholder():
    assert _FORMAT_PLACEHOLDER.findall("for {stale_days} days") == ["{stale_days}"]
    assert not _FORMAT_PLACEHOLDER.findall('{{ stale_days }} `{"add": [1]}`')


def test_no_template_keeps_a_format_placeholder():
    for path in prompting.PROMPTS_DIR.glob("*.j2"):
        assert not _FORMAT_PLACEHOLDER.findall(path.read_text()), path.name


def test_the_resolutions_sort_by_case_like_python():
    template = prompting._ENV.from_string(
        '{{ names | sort(case_sensitive=true) | join(",") }}'
    )
    assert template.render(names={"b", "B", "a"}) == ",".join(sorted({"b", "B", "a"}))
