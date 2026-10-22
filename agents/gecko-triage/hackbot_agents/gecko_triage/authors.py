"""The commit authors and bugs that a run's `mozilla_vcs` tool results named.

The run may needinfo a commit author: the author of a restricted regressor, who sets
Regressed by because the bug cannot be named in public, or the developer who knows the
code when no expert's area matches. The PostToolUse hook collects, from each
`get_commit_info`, `file_history` and `get_commit_diff` result on a main repo, the
author emails, which the needinfo check accepts, and the bug numbers of the commit
messages. The post-run
check reads those bugs, and the comment must not contain one that is restricted.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from agent_tools.mozilla_vcs import MozillaVcsContext

from .closing import response_texts
from .targets import explicit_bug_ids
from .teamconfig import EMAIL

# The tools that show a commit: its author, as the hg `user` "Name <email>" in
# `author`, and its message, in `desc` or in the hg export text of `diff`.
COMMIT_TOOLS = (
    "mcp__mozilla_vcs__get_commit_info",
    "mcp__mozilla_vcs__file_history",
    "mcp__mozilla_vcs__get_commit_diff",
)
COMMIT_TOOLS_MATCHER = "|".join(re.escape(t) for t in COMMIT_TOOLS)
_ANGLE_EMAIL = re.compile(rf"<({EMAIL.pattern})>")
# The short form "b=N" a commit message may use besides the explicit references.
_SHORT_BUG = re.compile(r"\bb=(\d+)", re.IGNORECASE)
# The range of real bug ids; a smaller or larger number is not one.
BUG_ID_RANGE = range(100_000, 10_000_000)
# The repos whose commits landed: a try push names anyone.
MAIN_REPO = re.compile(
    r"(?:integration/)?autoland|mozilla-central"
    r"|(?:releases/)?mozilla-(?:beta|release|esr\d+)"
)
DEFAULT_REPO = MozillaVcsContext().default_repo


def is_main_repo(tool_input: object) -> bool:
    """Whether a vcs call reads a repo whose commits landed; no repo is the default."""
    repo = tool_input.get("repo") if isinstance(tool_input, dict) else None
    repo = repo or DEFAULT_REPO
    return isinstance(repo, str) and bool(MAIN_REPO.fullmatch(repo.strip()))


@dataclass
class CommitFacts:
    """What the run's commit results named: author emails and commit message bugs."""

    authors: set[str] = field(default_factory=set)
    bugs: set[int] = field(default_factory=set)


def _entries(response: object) -> Iterator[dict]:
    """The commit of a result and each of its `changesets` entries."""
    for text in response_texts(response):
        try:
            data = json.loads(text) if isinstance(text, str) else None
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        changesets = data.get("changesets")
        for entry in [data, *(changesets if isinstance(changesets, list) else ())]:
            if isinstance(entry, dict):
                yield entry


def commit_author_emails(response: object) -> set[str]:
    """The first `<email>` of each `author` field of a commit tool result, lower case.

    Only the `author` of the commit and of each `changesets` entry counts, never an
    address that a commit message quotes.
    """
    emails = set()
    for entry in _entries(response):
        author = entry.get("author")
        match = _ANGLE_EMAIL.search(author) if isinstance(author, str) else None
        if match:
            emails.add(match[1].lower())
    return emails


def _in_range(ids: set[int]) -> set[int]:
    return {n for n in ids if n in BUG_ID_RANGE}


def message_bug_ids(message: str) -> set[int]:
    """Every bug number in a commit message, within `BUG_ID_RANGE`.

    The explicit references of `targets.explicit_bug_ids`, and "b=N".
    """
    short = {int(n) for n in _SHORT_BUG.findall(message)}
    return _in_range(explicit_bug_ids(message) | short)


def commit_bug_ids(response: object) -> set[int]:
    """The bug numbers in the commit messages of a commit tool result.

    A message is the `desc` of a commit, or the hg export text of `diff` before the
    first file diff. The file diffs add only their explicit references, since a bare
    number there may be a test file name.
    """
    ids = set()
    for entry in _entries(response):
        if isinstance(desc := entry.get("desc"), str):
            ids |= message_bug_ids(desc)
        if isinstance(diff := entry.get("diff"), str):
            head, _, body = diff.partition("\ndiff ")
            ids |= message_bug_ids(head) | _in_range(explicit_bug_ids(body))
    return ids


def commit_hook(facts: CommitFacts):
    """The PostToolUse hook on `COMMIT_TOOLS` that collects ``facts``."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        if is_main_repo(input_data.get("tool_input")):
            response = input_data.get("tool_response")
            facts.authors.update(commit_author_emails(response))
            facts.bugs.update(commit_bug_ids(response))
        return {}

    return hook
