"""Cheap lexical similarity, used to spot duplicate gaps and repeated questions."""

import re

_STOP = frozenset(
    "a an and are as at be by can could do does for from has have how if in is it its "
    "of on or that the this to was were what when where which while who why will with "
    "would crash crashes".split()
)
_SUFFIXES = ("ies", "ing", "ed", "s")


def _stem(word: str) -> str:
    """Strip common English suffixes from plain words so that "conditions" and
    "condition" match; identifiers (with ``::`` or ``_``) are left whole."""
    if len(word) <= 4 or not word.isalpha():
        return word
    for suffix in _SUFFIXES:
        if word.endswith("ss"):
            break
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def tokens(text: str) -> set[str]:
    words = [
        w.strip(":") for w in re.findall(r"[A-Za-z_][A-Za-z0-9_:]*|\d+", text.lower())
    ]
    return {_stem(w) for w in words if w not in _STOP and len(w) > 1}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
