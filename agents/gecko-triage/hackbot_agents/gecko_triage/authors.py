"""The commit authors and bugs that a run's `mozilla_vcs` tool results named.

The run may needinfo a commit author: the author of a restricted regressor, who sets
Regressed by because the bug cannot be named in public, or the developer who knows the
code when no expert's area matches. The PostToolUse hook collects, from each
`get_commit_info`, `file_history` and `get_commit_diff` result on a main repo, the
author emails, which the needinfo check accepts, and the bug numbers of the commit
messages. The post-run
check reads those bugs, and the comment must not contain one that is restricted.

A commit email is often not the author's Bugzilla login. The PostToolUse hook on
`get_bugs` collects the @mozilla.com assignee of each bug that a commit message names.
The needinfo check also accepts that assignee when their real name is the name of that
commit's author, so a backout or a "regressed by" bug makes nobody else askable.
"""

import json
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field

from agent_tools.mozilla_vcs import MozillaVcsContext

from .actions_view import is_bug_id
from .closing import response_texts
from .targets import explicit_bug_ids
from .teamconfig import EMAIL
from .withheld import GET_BUGS_TOOL, payloads

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
# The "[:nick]" and "(...)" parts of a Bugzilla real name.
_NAME_EXTRAS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
# An unclosed trailing "[:nick" or a trailing ", :nick" left after `_NAME_EXTRAS`.
_TRAILING_NICK = re.compile(r"\[[^\]]*$|(?:,\s*|\s+):\S*\s*$")
_QUOTES = str.maketrans("", "", "\"'`\u2018\u2019\u201c\u201d")
MOZILLA_DOMAIN = "@mozilla.com"
NOBODY = "nobody@mozilla.org"


def normalized_name(name: object) -> str:
    """A person's name to compare: no nick, "(...)" part, quote or accent; casefolded."""
    if not isinstance(name, str):
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = _NAME_EXTRAS.sub(" ", name.translate(_QUOTES))
    while (shorter := _TRAILING_NICK.sub("", name)) != name:
        name = shorter
    return " ".join(name.split()).casefold()


def is_main_repo(tool_input: object) -> bool:
    """Whether a vcs call reads a repo whose commits landed; no repo is the default."""
    repo = tool_input.get("repo") if isinstance(tool_input, dict) else None
    repo = repo or DEFAULT_REPO
    return isinstance(repo, str) and bool(MAIN_REPO.fullmatch(repo.strip()))


@dataclass
class CommitFacts:
    """What the run's commit results named: author emails and commit message bugs.

    ``bug_authors`` maps each commit message bug to the normalized names of the
    authors of the commits that name it. ``assignees`` maps each bug the run read to
    its @mozilla.com assignee's email and normalized real name. An assignee whose
    name is an author name of that bug is in ``assignee_authors`` and ``authors``.
    ``commit_emails`` are the author emails of the commit results themselves.
    """

    authors: set[str] = field(default_factory=set)
    bugs: set[int] = field(default_factory=set)
    bug_authors: dict[int, set[str]] = field(default_factory=dict)
    assignees: dict[int, tuple[str, str]] = field(default_factory=dict)
    assignee_authors: set[str] = field(default_factory=set)
    commit_emails: set[str] = field(default_factory=set)

    def add(
        self,
        authors: set[str] = frozenset(),
        bug_authors: dict[int, set[str]] | None = None,
        assignees: dict[int, tuple[str, str]] | None = None,
    ) -> None:
        self.authors |= authors
        self.commit_emails |= authors
        for bug, names in (bug_authors or {}).items():
            self.bugs.add(bug)
            self.bug_authors.setdefault(bug, set()).update(n for n in names if n)
        self.assignees |= assignees or {}
        for bug, (email, name) in self.assignees.items():
            if name and name in self.bug_authors.get(bug, ()):
                self.assignee_authors.add(email)
                self.authors.add(email)


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


def _entry_bug_ids(entry: dict) -> set[int]:
    ids = set()
    if isinstance(desc := entry.get("desc"), str):
        ids |= message_bug_ids(desc)
    if isinstance(diff := entry.get("diff"), str):
        head, _, body = diff.partition("\ndiff ")
        ids |= message_bug_ids(head) | _in_range(explicit_bug_ids(body))
    return ids


def commit_bug_ids(response: object) -> set[int]:
    """The bug numbers in the commit messages of a commit tool result.

    A message is the `desc` of a commit, or the hg export text of `diff` before the
    first file diff. The file diffs add only their explicit references, since a bare
    number there may be a test file name.
    """
    return set(commit_bug_authors(response))


def commit_bug_authors(response: object) -> dict[int, set[str]]:
    """Each bug of `commit_bug_ids`, with the normalized names of its commits' authors.

    The name is the `author` text before the `<email>`; a bug of a commit with no
    name maps to no name.
    """
    found: dict[int, set[str]] = {}
    for entry in _entries(response):
        author = entry.get("author")
        name = (
            normalized_name(author.partition("<")[0]) if isinstance(author, str) else ""
        )
        for bug in _entry_bug_ids(entry):
            found.setdefault(bug, set()).update({name} - {""})
    return found


def commit_hook(facts: CommitFacts):
    """The PostToolUse hook on `COMMIT_TOOLS` that collects ``facts``."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        if is_main_repo(input_data.get("tool_input")):
            response = input_data.get("tool_response")
            facts.add(commit_author_emails(response), commit_bug_authors(response))
        return {}

    return hook


def mozilla_assignees(response: object) -> dict[int, tuple[str, str]]:
    """The @mozilla.com assignee of each bug of a `get_bugs` result.

    The email, lower case, is `assigned_to`, else `assigned_to_detail.email`; the
    name is the normalized `assigned_to_detail.real_name`, empty when missing.
    """
    found = {}
    for payload in payloads(response):
        bugs = payload.get("bugs")
        for bug in bugs if isinstance(bugs, list) else ():
            if not isinstance(bug, dict) or not is_bug_id(bug.get("id")):
                continue
            detail = bug.get("assigned_to_detail")
            detail = detail if isinstance(detail, dict) else {}
            email = bug.get("assigned_to") or detail.get("email")
            email = email.strip().lower() if isinstance(email, str) else ""
            if email.endswith(MOZILLA_DOMAIN) and email != NOBODY:
                found[bug["id"]] = (email, normalized_name(detail.get("real_name")))
    return found


def assignee_hook(facts: CommitFacts):
    """The PostToolUse hook on `get_bugs` that adds the commit bugs' assignees."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        if input_data.get("tool_name") == GET_BUGS_TOOL:
            response = input_data.get("tool_response")
            facts.add(assignees=mozilla_assignees(response))
        return {}

    return hook
