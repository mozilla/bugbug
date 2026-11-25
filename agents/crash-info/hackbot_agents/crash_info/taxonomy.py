"""Load the crash missing-information taxonomy and render it for prompts.

The taxonomy lives in ``taxonomy.toml`` next to this module so researchers can
edit definitions without touching code. A label renders as its full definition,
or as a one-line card that keeps prompts small for local models.
"""

import re
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from .models import ActionType

TAXONOMY_PATH = Path(__file__).with_name("taxonomy.toml")

_PHASES = {"initial", "late"}
_RECOVERABLE_BY = {"evidence", "either", "developer"}


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    focus: str


@dataclass(frozen=True)
class Label:
    id: str
    name: str
    category: str
    definition: str
    canonical_question: str
    include_when: str
    exclude_when: str
    distinguish: str
    ask: str
    cue: str
    nearby: tuple[str, ...]
    phase: str
    actions: tuple[ActionType, ...]
    recoverable_by: str
    # The name in plain words, for people: reports and the UI, not prompts.
    plain: str = ""

    def card_line(self) -> str:
        return f"- {self.id} ({self.name}): {self.ask} Cue: {self.cue}"

    def full_text(self) -> str:
        return (
            f"{self.id} ({self.name})\n"
            f"  Definition: {self.definition}\n"
            f"  Canonical question: {self.canonical_question}\n"
            f"  Include when: {self.include_when}\n"
            f"  Do not include when: {self.exclude_when}\n"
            f"  Distinguish: {self.distinguish}"
        )


@dataclass(frozen=True)
class Taxonomy:
    version: str
    categories: tuple[Category, ...]
    labels: tuple[Label, ...]

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(label.id for label in self.labels)

    def label(self, label_id: str) -> Label:
        for label in self.labels:
            if label.id == label_id:
                return label
        raise KeyError(f"unknown taxonomy label {label_id!r}")

    def category(self, category_id: str) -> Category:
        for category in self.categories:
            if category.id == category_id:
                return category
        raise KeyError(f"unknown taxonomy category {category_id!r}")

    def in_category(self, category_id: str) -> list[Label]:
        return [label for label in self.labels if label.category == category_id]

    def normalize(self, raw: str | None) -> str | None:
        """Map a model's spelling of a label ("obj.state", "OBJ_STATE",
        "Object state & identity") to its canonical id, or None."""
        if not raw:
            return None
        key = _squash(raw)
        for label in self.labels:
            if key in (_squash(label.id), _squash(label.name)):
                return label.id
        # "Obj.STATE (Object state & identity)" and similar decorated forms.
        for label in self.labels:
            if _squash(label.id) in key:
                return label.id
        return None


def plain_name(label_id: str | None) -> str:
    """A label as people read it ("Which cases the fix must cover" for
    Repair.GUARD); its id when the bundled taxonomy does not know it."""
    if not label_id:
        return "Unlabelled"
    try:
        label = _default_taxonomy().label(label_id)
    except KeyError:
        return label_id
    return label.plain or label.name


_LABEL_TAG = re.compile(r"\[([A-Za-z]+\.[A-Z_]+)\]")


def with_plain_names(text: str) -> str:
    """Markdown with each "[Repair.GUARD]" tag turned into its plain name, in bold."""

    def name(match: re.Match[str]) -> str:
        plain = plain_name(match.group(1))
        return match.group(0) if plain == match.group(1) else f"**{plain}**:"

    return _LABEL_TAG.sub(name, text)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _validate(taxonomy: Taxonomy) -> None:
    ids = taxonomy.ids
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate taxonomy label ids")
    category_ids = {c.id for c in taxonomy.categories}
    for label in taxonomy.labels:
        if label.category not in category_ids:
            raise ValueError(f"{label.id}: unknown category {label.category!r}")
        if not label.id.startswith(label.category + "."):
            raise ValueError(f"{label.id}: id does not start with its category")
        unknown = set(label.nearby) - set(ids)
        if unknown:
            raise ValueError(f"{label.id}: unknown nearby labels {sorted(unknown)}")
        if label.phase not in _PHASES:
            raise ValueError(f"{label.id}: phase must be one of {sorted(_PHASES)}")
        if label.recoverable_by not in _RECOVERABLE_BY:
            raise ValueError(f"{label.id}: bad recoverable_by {label.recoverable_by!r}")


def load_taxonomy(path: Path | None = None) -> Taxonomy:
    if path is None:
        return _default_taxonomy()
    return _load(path)


@cache
def _default_taxonomy() -> Taxonomy:
    return _load(TAXONOMY_PATH)


def _load(path: Path) -> Taxonomy:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    taxonomy = Taxonomy(
        version=str(data["version"]),
        categories=tuple(Category(**c) for c in data["categories"]),
        labels=tuple(
            Label(
                **{
                    **raw,
                    "nearby": tuple(raw.get("nearby", ())),
                    "actions": tuple(ActionType(a) for a in raw.get("actions", ())),
                }
            )
            for raw in data["labels"]
        ),
    )
    _validate(taxonomy)
    return taxonomy
