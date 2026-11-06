"""Render the prompt templates in `prompts/` with Jinja.

A template gets its values as keyword arguments. A missing value fails the render, so
a typo in a template never reaches the model as an empty string.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

_ENV = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    auto_reload=False,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
)
_ENV.filters["code"] = lambda value: f"`{value}`"


def description(name: str, **values: object) -> str:
    """The description of the subagent ``name``, from `prompts/descriptions.j2`."""
    return str(getattr(_ENV.get_template("descriptions.j2").module, name)(**values))


def render(name: str, **values: object) -> str:
    """`prompts/<name>.md.j2` with ``values`` filled in."""
    return _ENV.get_template(f"{name}.md.j2").render(**values)
