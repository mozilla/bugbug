"""Prompt templates (``prompts/*.md``, ``$placeholder`` syntax) and helpers to
keep the variable parts of a prompt inside the model's context window."""

from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).with_name("prompts")

# Loaded once, at import: a long run keeps the prompts its code was written for,
# even if the files are edited meanwhile.
_TEMPLATES = {
    path.stem: Template(path.read_text(encoding="utf-8"))
    for path in sorted(PROMPTS_DIR.glob("*.md"))
}


def render(name: str, **values: object) -> str:
    return _TEMPLATES[name].substitute(**{k: str(v) for k, v in values.items()}).strip()


def clip(text: str, limit: int) -> str:
    """Shorten ``text`` to at most ``limit`` characters, marking the cut."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 16)].rstrip() + " …[truncated]"


def one_line(text: str, limit: int = 200) -> str:
    return clip(" ".join(text.split()), limit)
