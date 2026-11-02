"""The comment numbers that a triage comment cites.

A cited comment of another bug must be one that the run read, and a sentence that
cites a comment for a patch or an attachment must cite the comment that adds it or
lands it. The hook on the broker tools notes each comment that a `get_bug_comments` or
a `get_bugs` result holds, also from the file a large result is saved to. The triaged
bug's comments come from the run's state.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .actions_view import is_bug_id
from .withheld import GET_BUGS_TOOL, payloads, spilled_text

COMMENTS_TOOL = "mcp__bugzilla__get_bug_comments"

_CROSS = (
    re.compile(
        r"\bbug\s+(?P<bug>\d+)(?:'s)?\s*,?\s*\(?comment\s+#?(?P<n>\d+)\)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcomment\s+#?(?P<n>\d+)\s+(?:of|in|on|from)\s+bug\s+(?P<bug>\d+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bbug\s+(?P<bug>\d+)#c(?P<n>\d+)\b", re.IGNORECASE),
)
_OWN = re.compile(r"\bcomment\s+#?(?P<n>\d+)\b", re.IGNORECASE)
_PATCH = re.compile(r"\b(?:patch(?:es)?|attachments?)\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_LANDING = re.compile(
    r"https://(?:hg\.mozilla\.org/\S+/rev/|github\.com/mozilla-firefox/firefox/commit/)"
)
_BUGZILLA_COMMENT = re.compile(
    r"https?://bugzilla\.mozilla\.org/(?:show_bug\.cgi\?id=)?(\d+)#c(\d+)\b"
)
_MARKDOWN_LINK = re.compile(r"!?\[([^\]\n]*)\]\(([^()\s]*)\)")


@dataclass
class ReadComments:
    """Each comment the run read, as (bug, comment number).

    The value says whether the comment adds an attachment or lands a change, so that a
    sentence may cite it for a patch.
    """

    seen: dict[tuple[int, int], bool] = field(default_factory=dict)


def _adds_change(comment: dict) -> bool:
    text = comment.get("text")
    return bool(comment.get("attachment_id")) or (
        isinstance(text, str) and _LANDING.search(text) is not None
    )


def note_comments(store: ReadComments, bug: object, comments: Iterable[object]) -> None:
    if not is_bug_id(bug):
        return
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        count = comment.get("count")
        if isinstance(count, int) and not isinstance(count, bool):
            store.seen[(bug, count)] = _adds_change(comment)


def note_read(store: ReadComments, input_data: dict) -> None:
    tool = input_data.get("tool_name")
    if tool not in (COMMENTS_TOOL, GET_BUGS_TOOL):
        return
    response = input_data.get("tool_response")
    if (spilled := spilled_text(response)) is not None:
        response = spilled
    for payload in payloads(response):
        if tool == COMMENTS_TOOL:
            note_comments(store, payload.get("bug_id"), payload.get("comments") or ())
            continue
        for bug in payload.get("bugs") or ():
            if isinstance(bug, dict):
                note_comments(store, bug.get("id"), bug.get("comments") or ())


def read_hook(store: ReadComments):
    """The PostToolUse hook on the broker tools."""

    async def hook(input_data: dict, tool_use_id: str | None, context) -> dict:
        note_read(store, input_data)
        return {}

    return hook


def citable_text(body: str) -> str:
    """``body`` with its links in a form that keeps their citations.

    A markdown link becomes its text, and a Bugzilla comment URL the comment it cites.
    """

    def link(match: re.Match) -> str:
        cited = _BUGZILLA_COMMENT.search(match[2])
        return match[1] + (f" (bug {cited[1]} comment {cited[2]})" if cited else "")

    return _BUGZILLA_COMMENT.sub(r"bug \1 comment \2", _MARKDOWN_LINK.sub(link, body))


def _citations(sentence: str, bug: int) -> list[tuple[int, int, int]]:
    """Each (bug, comment number, position) that ``sentence`` cites."""
    cited = []
    for pattern in _CROSS:
        for m in pattern.finditer(sentence):
            cited.append((int(m["bug"]), int(m["n"]), m.start()))
        sentence = pattern.sub(lambda m: " " * len(m[0]), sentence)
    cited += [(bug, int(m["n"]), m.start()) for m in _OWN.finditer(sentence)]
    return cited


def citation_refusal(text: str, bug: int, store: ReadComments) -> str | None:
    """Why the comment cites a comment wrongly, or None.

    A patch or an attachment is checked against the nearest citation after its word, as
    in "the patch on bug N (comment 4)". A citation before the word, as in "comment 5
    says that the patch works", is not checked for an attachment.
    """
    own_known = any(b == bug for b, _ in store.seen)
    for sentence in _SENTENCE_END.split(text):
        cited = _citations(sentence, bug)
        for cited_bug, count, _ in cited:
            if (cited_bug, count) in store.seen:
                continue
            if cited_bug == bug and not own_known:
                continue
            return (
                f"your comment cites comment {count} of bug {cited_bug}, which this run "
                f"did not read. Read it with get_bug_comments (start={count}, limit=1) "
                "before you cite it, or remove the citation"
            )
        if not cited:
            continue
        for patch in _PATCH.finditer(sentence):
            after = [c for c in cited if c[2] > patch.start()]
            if not after:
                continue
            cited_bug, count, _ = min(after, key=lambda c: c[2])
            if store.seen.get((cited_bug, count), True):
                continue
            return (
                f"comment {count} of bug {cited_bug} adds no attachment, but your "
                "sentence cites it for a patch or an attachment. Cite the comment that "
                'adds it, the one that starts with "Created attachment", or the one '
                "that lands it"
            )
    return None
