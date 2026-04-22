from __future__ import annotations

import re
from pathlib import Path

import yaml

# Read-only Bugzilla surface.
BUGZILLA_READ_TOOLS = [
    "mcp__bugzilla__search_bugs",
    "mcp__bugzilla__get_bugs",
    "mcp__bugzilla__get_bug_comments",
    "mcp__bugzilla__get_bug_attachments",
]

# The VERDICT: line the agent is told to emit.
_VERDICT_RE = re.compile(
    r"^VERDICT:\s*"
    r"(?:bug\s*)?"
    r"(?:https?://\S+?id=)?"
    r"(NEW|\d+)\b",
    re.IGNORECASE | re.MULTILINE,
)

# --local-to-local verdicts name a directory, not a bug ID.
_VERDICT_LINE_RE = re.compile(r"^VERDICT:\s*(.+?)\s*$", re.MULTILINE)

_CONFIG_KEYS = {"base_url", "model", "max_turns"}


def load_config(path: Path) -> dict:
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    return {k: v for k, v in data.items() if k in _CONFIG_KEYS}


def parse_verdict(text: str) -> str | None:
    matches = _VERDICT_RE.findall(text)
    if not matches:
        return None
    v = matches[-1].upper()
    return "NEW" if v == "NEW" else v


def parse_dir_verdict(text: str, candidates: set[str]) -> str | None:
    matches = _VERDICT_LINE_RE.findall(text)
    if not matches:
        return None
    v = matches[-1]
    if v.upper() == "NEW":
        return "NEW"
    v = v.rstrip("/")
    return v if v in candidates else None
