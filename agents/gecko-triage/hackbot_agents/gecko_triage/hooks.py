"""Record-time limits on what this agent may write to a bug.

Once the agent is automatic, a run's actions reach Bugzilla with no person in between, and an action's
params are model output. These hooks bound that at the moment of recording, so a
refusal reaches the agent as a tool error it can correct in the same run and the
out-of-bounds action never lands in ``summary.json``. ``ActionsRecorder`` runs hooks
before appending, so raising here aborts the recording.

What they enforce:

- one public comment per run, on the triaged bug; `rerecord.py` lets the main agent
  remove every recorded action and record them again, and every hook then runs again;
- no retyped capture steps, no list item that names the capture marker, and no request
  that names the marker without the verbatim capture block;
- the ASD-STE100 limits on the prose: 25 words per sentence, 6 sentences per paragraph,
  no dash as an aside and no severity or priority label. The limits skip code, quoted
  and indented lines, link text, and the capture and mozregression blocks;
- links outside code only as http or https to the hosts in `ALLOWED_LINK_HOSTS`, in
  every markdown form, and no raw HTML link;
- severity and priority only when the current value is `--`, never S1, P4 or P5;
- at most three needinfos, each on one full email address, never the bot, never twice,
  not on someone with one open, and only on someone on the bug, a listed expert of the
  team, anyone when the bug moves, or a commit author that a `mozilla_vcs` result of
  the run named or the @mozilla.com assignee of its commit message bug (the run then
  waits for a person);
- the first needinfo on a reporter who is not a developer or an expert is refused when
  no `bug_investigator` ran before it, and a second attempt passes (see
  `investigations.py`);
- each needinfo carries only name, status and requestee, and the hook adds
  `"new": true` (see docs/bugzilla.md);
- `blocks` only adds one or two meta bugs from the team's `meta-bugs.md`;
- `keywords` only adds one of the keywords `shared/bugzilla.toml` allows;
- `regressed_by` only adds another bug's id;
- `see_also` only adds the id of another bug that its see_also does not name yet;
- `whiteboard` only adds the fix tags of `shared/bugzilla.toml` that the bug lacks, the
  good-first-bug candidate tag only with the easy-fix tag, never on a close or a move.
  The hook records the bug's whiteboard with the tags at its end, since Bugzilla
  replaces the whole text. The post-run check drops it on a run that is not `analyzed`;
- a move names a target component other than the current one, and a move to another
  product carries the version and milestone that exist in every product;
- one field-change call on the triaged bug per run, since the apply step merges calls
  field by field;
- on a DUPLICATE, one more change on the `dupe_of` bug that only adds one team meta bug
  to `blocks`; the duplicate itself blocks no meta bug, and never duplicates one;
- with no team, no `blocks` change at all, no change on another bug, and no capture
  placeholder, since there is no meta bug list and no capture block;
- `dupe_of` never a team meta bug, a duplicate candidate that is a meta bug, or a bug
  the triaged bug blocks; the post-run check in `targets.py` catches any other meta bug;
- RESOLVED only as INVALID, WONTFIX, FIXED, DUPLICATE (with `dupe_of`) or INCOMPLETE,
  and INCOMPLETE only when `TriageState.incomplete_allowed` holds: every open needinfo
  is on the reporter, who has been quiet for `STALE_NEEDINFO_DAYS`;
- RESOLVED only after `close_verifier` confirmed that resolution, and that `dupe_of` on a
  DUPLICATE, in this run and before the comment, and never after it refuted a close
  (see `closing.py`). A move needs no verdict;
- after a refute, no comment that names a resolution in any case or asks to close the
  bug;
- every other field refused by name.
"""

import re
from collections.abc import Collection, Set
from pathlib import Path

from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionHook, ActionsRecorder
from hackbot_runtime.actions.bugzilla import COMMENT_FOOTER_SEPARATOR
from hackbot_runtime.searchfox import PLACEHOLDER as SEARCHFOX_PLACEHOLDER
from hackbot_runtime.searchfox import permalink_hook, permalink_prefix

from .actions_view import action_params, is_bug_id, needinfo_requestees, updates
from .closing import (
    CloseVerdicts,
    close_verdict_refusal,
    comment_marker_hook,
)
from .config import (
    ALLOWED_LINK_HOSTS,
    ALLOWED_UPDATE_FIELDS,
    CAPTURE,
    CAPTURE_PLACEHOLDER,
    CROSS_PRODUCT_DEFAULTS,
    MAX_META_BUGS,
    MAX_NEEDINFOS,
    MAX_SENTENCES_PER_PARAGRAPH,
    MAX_WORDS_PER_SENTENCE,
    MOVE_FIELDS,
    MOZREGRESSION_BLOCK,
    MOZREGRESSION_PLACEHOLDER,
    NEEDINFO_EXPERTS,
    NEEDINFO_FALLBACK,
    RESOLUTION_FIELDS,
    TEAM,
    TEAM_META_BUGS,
    TERMS,
    TRIAGE_PRIORITIES,
    TRIAGE_RESOLUTIONS,
    TRIAGE_SEVERITIES,
    UNSET_FIELD_VALUE,
)
from .investigations import Investigations, early_ask_refusal
from .rerecord import REMOVE_RECORDED_ACTIONS_TOOL
from .state import TriageState
from .teamconfig import EMAIL


def _check_target_bug(params: dict, bug_id: int) -> None:
    if params.get("bug_id") != bug_id:
        raise ToolError(
            f"you are triaging bug {bug_id}; record actions against that bug, "
            f"not bug {params.get('bug_id')!r}"
        )


def _recorded(recorder: ActionsRecorder, action_type: str) -> list[dict]:
    return [a for a in recorder.actions if a["type"] == action_type]


# --- bugzilla.add_comment --------------------------------------------------- #


def add_comment_hook(recorder: ActionsRecorder, bug_id: int) -> ActionHook:
    """One public comment, on the bug being triaged."""

    def hook(action: dict) -> None:
        params = action_params(action)
        if _recorded(recorder, "bugzilla.add_comment"):
            raise ToolError(
                "you have already recorded a comment; record one per run. To fix "
                f"it, call `{REMOVE_RECORDED_ACTIONS_TOOL}`, then record the comment "
                "and every field change again"
            )
        _check_target_bug(params, bug_id)
        if params.get("is_private"):
            raise ToolError(
                "record the comment publicly: the reporter needs to read it"
            )

    return hook


def block_placeholder_hook(action: dict) -> None:
    """Expand the capture and mozregression placeholders into their verbatim blocks."""
    params = action_params(action)
    text = params.get("text")
    if not isinstance(text, str):
        return
    if CAPTURE is None:
        if CAPTURE_PLACEHOLDER in text:
            raise ToolError(
                f"this run has no team, so it has no capture block; remove "
                f"`{CAPTURE_PLACEHOLDER}` and ask for the data in plain words"
            )
    else:
        text = text.replace(CAPTURE_PLACEHOLDER, CAPTURE.capture_block)
    params["text"] = text.replace(MOZREGRESSION_PLACEHOLDER, MOZREGRESSION_BLOCK)


def split_runtime_footer(text: str) -> tuple[str, str]:
    """``(agent's text, runtime's trailer)``; the trailer is empty when absent."""
    head, sep, tail = text.rpartition(COMMENT_FOOTER_SEPARATOR)
    if not sep:
        return text, ""
    return head, sep + tail


_FENCED_CODE = re.compile(
    r"^ {0,3}(?P<fence>`{3,}(?=[^`\n]*$)|~{3,})[^\n]*\n.*?"
    r"(?:^ {0,3}(?P=fence)(?:(?<=`)`*|(?<=~)~*)[ \t]*$|\Z)",
    re.DOTALL | re.MULTILINE,
)
_INLINE_CODE = re.compile(
    r"(?<!`)(?P<ticks>`+)(?!`)(?P<code>[^\n]+?)(?<!`)(?P=ticks)(?!`)"
)
_HTML_TAG = (
    r"<[a-z][a-z0-9-]*(?:\s+[a-z_:][\w.:-]*"
    r"(?:\s*=\s*(?:[^\s\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*\s*/?>"
    r"|</[a-z][a-z0-9-]*\s*>"
)
# The inline forms a code span cannot hide: a link target, an autolink and a raw HTML
# tag. The earliest match wins, as in a CommonMark parser.
_INLINE = re.compile(
    r"(?P<target>\]\(\s*(?:<[^>\n]*>|[^\s()]*(?:\([^\s()]*\)[^\s()]*)*))"
    r"|(?P<autolink><[a-z][a-z0-9+.-]{1,31}:[^\s<>]*>)"
    rf"|(?P<html>{_HTML_TAG})"
    r"|" + _INLINE_CODE.pattern,
    re.IGNORECASE,
)
_REFERENCE = re.compile(r"^ {0,3}\[[^\]\n]+\]:[ \t]*\n?[ \t]*(\S+)", re.MULTILINE)
_HTML_LINK = re.compile(
    r"[\s/](?:href|src|srcset|action|formaction)\s*=", re.IGNORECASE
)
_BARE_URL = re.compile(
    r"(?<![a-z0-9+.-])(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>]+", re.IGNORECASE
)
_TRAILING_PUNCTUATION = ".,:;!?*_~'\")]}"
_AUTHORITY = re.compile(
    r"https?://(?P<host>[a-z0-9.-]+)(?::\d*)?(?:[/?#]|$)", re.IGNORECASE
)
_PLACEHOLDER_TARGET = f"{SEARCHFOX_PLACEHOLDER}/"


def _host_allowed(host: str) -> bool:
    for allowed in ALLOWED_LINK_HOSTS:
        if allowed.startswith("*."):
            if host == allowed[2:] or host.endswith(allowed[1:]):
                return True
        elif host == allowed:
            return True
    return False


def link_host_allowed(url: str) -> bool:
    """Whether ``url`` is an http or https link to an allowed host.

    The authority must hold a plain host name and an optional port. A backslash, user
    info, quotes or any other scheme fails the check.
    """
    url = url.strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1]
    if not url or url.startswith(_PLACEHOLDER_TARGET):
        return True
    if url[:4].lower() == "www.":
        url = f"https://{url}"
    match = _AUTHORITY.match(url)
    if not match or "\\" in url:
        return False
    return _host_allowed(match["host"].lower())


def _without_quoted_blocks(text: str) -> str:
    """``text`` without the capture and mozregression blocks, which are ours verbatim."""
    if CAPTURE is not None:
        text = text.replace(CAPTURE.capture_block, " ")
    return text.replace(MOZREGRESSION_BLOCK, " ")


def _without_code(text: str) -> tuple[str, list[str]]:
    """``text`` without fenced and inline code, and the targets parsed before code."""
    text = _FENCED_CODE.sub("\n", text)
    targets = [m[1] for m in _REFERENCE.finditer(text)]

    def replace(match: re.Match) -> str:
        if match["code"] is not None:
            return " "
        if match["target"]:
            targets.append(match["target"][2:])
        elif match["autolink"]:
            targets.append(match["autolink"])
        elif _HTML_LINK.search(match["html"]):
            targets.append(match["html"])
        return match[0]

    return _INLINE.sub(replace, text), targets


def link_targets(text: str) -> list[str]:
    """Every link target in the markdown ``text`` outside code.

    Inline links, autolinks, reference definitions, bare URLs and raw HTML tags that
    carry a link. A raw HTML tag is returned whole and never passes the host check.
    """
    prose_text, targets = _without_code(text)
    for match in _BARE_URL.finditer(prose_text):
        targets.append(match[0].rstrip(_TRAILING_PUNCTUATION))
    return targets


def link_hook(action: dict) -> None:
    """Refuse a link to a host outside ``ALLOWED_LINK_HOSTS``; code and our blocks are quotes."""
    params = action_params(action)
    text = params.get("text")
    if not isinstance(text, str):
        return
    for url in link_targets(_without_quoted_blocks(text)):
        if not link_host_allowed(url):
            raise ToolError(
                f"your comment links to {url.strip()[:80]!r}. Link only to a host this "
                "agent allows: Mozilla, Searchfox, the profiler, the spec sites and the "
                "bug trackers. Use an https:// link with no user name and no raw HTML, "
                f"or write `{SEARCHFOX_PLACEHOLDER}` for a source file."
            )


_QUOTED_LINE = re.compile(r"^(?: {0,3}>| {4}|\t).*$", re.MULTILINE)
_LINK_TEXT = re.compile(r"!?\[[^\]\n]*\](?=[(\[])")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_DASH_ASIDE = re.compile(r"(?<=\S)\s[-–—]{1,2}\s|—")
_SENTENCE_END = re.compile(r"[.!?]+(?:\s+|$)")
_FIELD_LABEL = re.compile(r"(?<!\w)(?:S[1-4]|P[1-5])(?!\w)")
_WORD = re.compile(r"\w")


def prose(body: str) -> str:
    """The prose of a comment body, without the runtime's trailer.

    It leaves out our blocks, code, quoted and indented lines, link text and URLs.
    """
    body = _FENCED_CODE.sub("", _without_quoted_blocks(body))
    body = _INLINE_CODE.sub("code", _QUOTED_LINE.sub("", body))
    return _BARE_URL.sub(" ", _LINK_TEXT.sub("link", body))


def words(text: str) -> list[str]:
    return [w for w in text.split() if _WORD.search(w)]


def sentences(text: str) -> list[str]:
    found = []
    for line in text.splitlines():
        line = _LIST_MARKER.sub("", line)
        found.extend(s.strip() for s in _SENTENCE_END.split(line) if s and s.strip())
    return found


def paragraphs(text: str) -> list[str]:
    """Runs of prose lines between blank lines.

    List items are steps, not prose, so they end a paragraph and are not counted in one.
    """
    found, current = [], []
    for line in text.splitlines():
        if not line.strip() or _LIST_MARKER.match(line):
            if current:
                found.append(" ".join(current))
            current = []
        else:
            current.append(line)
    if current:
        found.append(" ".join(current))
    return found


_REQUEST = re.compile(
    r"\b(?:please|could you|can you|would you|we need|it would help|would be helpful)\b"
    r"|(?:^|[,;:]\s*|\bthen\s+)\W*(?:open|go to|choose|select|use|try|capture|record"
    r"|run|start|take|collect|attach|share|send|upload)\b",
    re.IGNORECASE,
)
_BLOCKQUOTE = re.compile(r"^ {0,3}>.*$", re.MULTILINE)
# The cues that a mention of the capture marker cites an existing capture: "attached" or
# "from" before it in its clause, "the" before it and "profile" after it, or "in comment
# N" right after it.
_CITED_BEFORE = re.compile(r"\battached\b|\bfrom\s*$")
_CITED_THE = re.compile(r"\bthe\s+$")
_CITED_KIND = re.compile(r"^\W*(?:profile|capture|log)\b")
_CITED_COMMENT = re.compile(r"^\W*(?:\w+\s+){0,2}in comment \d+")
_CLAUSE_START = re.compile(r".*[,;:]")


def _cites_capture(sentence: str, marker: str) -> bool:
    """Whether every mention of ``marker`` in ``sentence`` cites an existing capture."""
    text = sentence.lower().replace("`", "")
    start = 0
    while (at := text.find(marker, start)) >= 0:
        before = _CLAUSE_START.sub("", text[:at])
        after = text[at + len(marker) :]
        if not (
            _CITED_BEFORE.search(before)
            or (_CITED_THE.search(before) and _CITED_KIND.match(after))
            or _CITED_COMMENT.match(after)
        ):
            return False
        start = at + len(marker)
    return True


def _capture_refusal(problem: str, fix: str) -> ToolError:
    return ToolError(f"your comment {problem}. {fix}")


def capture_block_hook(action: dict) -> None:
    """Refuse retyped capture steps, and a request for the capture without the block.

    A comment may name the capture marker to cite an existing capture. Quoted lines are
    the reporter's words, not the agent's, so they are left out. With no team there is
    no capture block, so nothing is checked.
    """
    params = action_params(action)
    text = params.get("text")
    if CAPTURE is None or not isinstance(text, str):
        return
    body = split_runtime_footer(text)[0]
    has_block = CAPTURE.capture_block in body
    rest = _BLOCKQUOTE.sub("", _without_quoted_blocks(body))
    marker = CAPTURE.capture_marker
    expand = (
        f"Write `{CAPTURE_PLACEHOLDER}` on its own line where the steps belong, and the "
        "recorder expands it into the verbatim capture block"
    )
    lowered = rest.lower()
    if sum(label.lower() in lowered for label in CAPTURE.capture_labels) >= 2:
        raise _capture_refusal(f"retypes the {marker} capture steps", f"{expand}.")
    for line in rest.splitlines():
        if _LIST_MARKER.match(line) and marker in line.lower():
            fix = (
                f"Remove {marker} from the list item; the block names it."
                if has_block
                else f"{expand}. Do not put the steps in a list item or indent them."
            )
            raise _capture_refusal(f"names {marker} in a list item", fix)
    if has_block:
        return
    for sentence in sentences("\n".join(paragraphs(rest))):
        if (
            marker in sentence.lower()
            and _REQUEST.search(sentence)
            and not _cites_capture(sentence, marker)
        ):
            raise _capture_refusal(
                f"asks for {marker} without the capture block", f"{expand}."
            )


def comment_style_hook(action: dict) -> None:
    """The ASD-STE100 limits the comment rules set, applied to the comment as recorded."""
    params = action_params(action)
    text = params.get("text")
    if not isinstance(text, str):
        return
    body = prose(split_runtime_footer(text)[0])

    for line in body.splitlines():
        if _DASH_ASIDE.search(_LIST_MARKER.sub("", line)):
            raise ToolError(
                "your comment uses a dash as an aside; use a comma, a colon, or two "
                f"sentences (line: {line.strip()[:80]!r})"
            )

    if _FIELD_LABEL.search(body):
        raise ToolError(
            "your comment names a severity or priority level; those are Bugzilla "
            "fields, set with `bugzilla_update_bug`, and the label does not belong "
            "in the prose. Describe the user impact instead."
        )

    for sentence in sentences(body):
        count = len(words(sentence))
        if count > MAX_WORDS_PER_SENTENCE:
            raise ToolError(
                f"a sentence in your comment runs to {count} words; the limit is "
                f"{MAX_WORDS_PER_SENTENCE}. Split it: {sentence[:80]!r}"
            )

    for paragraph in paragraphs(body):
        count = len(sentences(paragraph))
        if count > MAX_SENTENCES_PER_PARAGRAPH:
            raise ToolError(
                f"a paragraph in your comment has {count} sentences; the limit is "
                f"{MAX_SENTENCES_PER_PARAGRAPH}. Split it or cut it: "
                f"{paragraph.strip()[:80]!r}"
            )


# The resolutions a comment may not name after a refute: the ones the agent may record,
# and the one the rules forbid it to propose.
CLOSE_WORDS = TRIAGE_RESOLUTIONS | {"WORKSFORME"}
_CLOSE_REQUEST = re.compile(
    rf"\b(?:{'|'.join(sorted(CLOSE_WORDS))})\b|\bduplicate of\b"
    r"|\b(?:close[sd]?|closing|resolve[sd]?) this bug\b|\bclosing is up to\b"
    r"|\bbe closed\b",
    re.IGNORECASE,
)


def refuted_close_hook(close_verdicts: CloseVerdicts) -> ActionHook:
    """After a refute, refuse a comment that still proposes a close."""

    def hook(action: dict) -> None:
        text = action_params(action).get("text")
        if not close_verdicts.refuted() or not isinstance(text, str):
            return
        if match := _CLOSE_REQUEST.search(prose(split_runtime_footer(text)[0])):
            raise ToolError(
                f"close_verifier refuted the close, but your comment still says "
                f"{match[0]!r}. Do not propose, recommend or ask for any close. Say "
                "what is still open and what the next step is"
            )

    return hook


# --- bugzilla.update_bug ---------------------------------------------------- #


def _check_level(
    field_name: str, value: object, current: object, allowed: frozenset[str]
) -> str:
    """The level, normalized; Bugzilla wants it in upper case."""
    level = value.strip().upper() if isinstance(value, str) else None
    if level not in allowed:
        raise ToolError(f"{field_name} must be one of {sorted(allowed)}, not {value!r}")
    if current != UNSET_FIELD_VALUE:
        raise ToolError(
            f"{field_name} is already {current!r}; the agent sets it only when it is "
            f"`{UNSET_FIELD_VALUE}`. If you disagree, say so in the comment."
        )
    return level


# Mozilla reserves severity N/A for these bug types; the agent leaves it to a person.
NO_SEVERITY_TYPES = frozenset({"task", "enhancement"})


def _check_severity(value: object, current: object, bug_type: object = None) -> str:
    if isinstance(bug_type, str) and bug_type.lower() in NO_SEVERITY_TYPES:
        raise ToolError(
            f"do not set severity on a bug of type {bug_type}: its severity is N/A, "
            "which a person sets. Leave severity unchanged."
        )
    if isinstance(value, str) and value.strip().upper() == "S1":
        raise ToolError(
            "never set S1: it forces P1 and nominates release tracking, which is a "
            "human's call. Record S2 and say in the summary why a person should "
            "consider S1."
        )
    return _check_level("severity", value, current, TRIAGE_SEVERITIES)


_REFUSED_PRIORITIES = {
    "P4": "it is reserved for web platform test bots",
    "P5": "it means will not fix, which is a person's decision",
}


def _check_priority(value: object, current: object) -> str:
    level = value.strip().upper() if isinstance(value, str) else None
    if level in _REFUSED_PRIORITIES:
        raise ToolError(
            f"never set {level}: {_REFUSED_PRIORITIES[level]}. Set P1, P2 or P3."
        )
    return _check_level("priority", value, current, TRIAGE_PRIORITIES)


_FLAG_KEYS = frozenset({"name", "status", "requestee"})


def _check_flags(
    changes: dict,
    state: TriageState,
    moving: bool,
    commit_authors: Set[str],
) -> None:
    """``moving`` is whether ``changes`` move the bug; ``_check_move`` checked it.

    ``commit_authors`` are the author emails the run's `mozilla_vcs` results named:
    the developer the code history finds, or the author of a restricted regressor,
    and the @mozilla.com assignees of their commit message bugs who have the commit
    author's name. A needinfo on one holds the run for a person, see
    `author_needinfos`.
    """
    flags = changes["flags"]
    if not isinstance(flags, list) or not flags:
        raise ToolError("flags must be a non-empty list of needinfo requests")
    if len(flags) > MAX_NEEDINFOS:
        raise ToolError(
            f"ask at most {MAX_NEEDINFOS} people per run; pick the ones who can answer"
        )
    already = {n.requestee for n in state.open_needinfos}
    askable = state.askable | commit_authors
    asked: set[str] = set()
    for flag in flags:
        if not isinstance(flag, dict) or set(flag) != _FLAG_KEYS:
            raise ToolError(
                "each flag must be an object with exactly name, status and requestee"
            )
        if flag["name"] != "needinfo" or flag["status"] != "?":
            raise ToolError(
                "the only flag this agent may set is a needinfo request: "
                '{"name": "needinfo", "status": "?", "requestee": "<email>"}'
            )
        requestee = flag["requestee"]
        if not isinstance(requestee, str) or not requestee.strip():
            raise ToolError("a needinfo needs a requestee email")
        email = requestee.strip().lower()
        if not EMAIL.fullmatch(email):
            raise ToolError(
                f"{requestee!r} is not one email address; use one full email per flag"
            )
        if email in state.bot_logins:
            raise ToolError("do not needinfo the bot itself")
        if email in already:
            raise ToolError(
                f"{requestee} already has an open needinfo on this bug; do not request "
                f"another, Bugzilla would collapse it anyway"
            )
        if email in asked:
            raise ToolError(f"ask {requestee} once")
        flag["requestee"] = email
        if email not in askable and not moving:
            raise ToolError(
                f"{requestee} is not on this bug and not on the team's expert list; ask "
                "someone on it, a listed expert, or a commit author that a "
                "mozilla_vcs file_history or get_commit_info result names, or the "
                "@mozilla.com assignee of a bug the commit names, when the assignee's "
                "real name is the name of that commit's author, or move "
                "the bug in the same change to ask the target team's developer"
            )
        asked.add(email)
    fallback = sorted((asked & NEEDINFO_FALLBACK) - NEEDINFO_EXPERTS - state.people)
    if len(fallback) > 1:
        raise ToolError(
            f"ask only one person from the fallback list, not {', '.join(fallback)}; "
            "first look for the code's owner with mozilla_vcs file_history or "
            "get_commit_info"
        )


def _check_reporter_ask(
    changes: dict, state: TriageState, investigations: Investigations
) -> None:
    if state.reporter_is_developer_or_expert:
        return
    reason = early_ask_refusal(
        investigations, state.reporter, needinfo_requestees(changes)
    )
    if reason:
        raise ToolError(
            f"{reason}. To change the recorded comment first, call "
            f"`{REMOVE_RECORDED_ACTIONS_TOOL}`"
        )


def author_needinfos(
    actions: list[dict], state: TriageState, commit_authors: Set[str]
) -> list[str]:
    """The needinfo requestees that only ``commit_authors`` made askable."""
    return sorted(
        {
            email
            for update in updates(actions)
            for email in needinfo_requestees(update.changes)
            if email in commit_authors and email not in state.askable
        }
    )


def _check_closing(
    changes: dict,
    state: TriageState,
    bug_id: int,
    meta_candidates: Set[int],
    close_verdicts: CloseVerdicts,
) -> None:
    if ("status" in changes) != ("resolution" in changes):
        raise ToolError("status and resolution travel together; set both or neither")
    resolution = changes.get("resolution")
    if changes.get("status") != "RESOLVED" or resolution not in TRIAGE_RESOLUTIONS:
        raise ToolError(
            f"the agent may only record RESOLVED with one of "
            f"{', '.join(sorted(TRIAGE_RESOLUTIONS))}; suggest anything else in the "
            "comment"
        )
    dupe_of = changes.get("dupe_of")
    if (resolution == "DUPLICATE") != ("dupe_of" in changes):
        raise ToolError("DUPLICATE and dupe_of travel together; set both or neither")
    if resolution == "DUPLICATE" and (not is_bug_id(dupe_of) or dupe_of == bug_id):
        raise ToolError("dupe_of must be the id of another bug")
    if resolution == "DUPLICATE" and dupe_of in TEAM_META_BUGS | meta_candidates:
        raise ToolError(
            f"dupe_of {dupe_of} is a meta bug; a bug blocks a meta bug, it does not "
            "duplicate one. Step 4b covers meta bugs"
        )
    if resolution == "DUPLICATE" and dupe_of in set(state.bug.get("blocks") or ()):
        raise ToolError(
            f"this bug blocks bug {dupe_of}, so it is not a duplicate of it; it is "
            "likely a meta or tracking bug"
        )
    if resolution == "INCOMPLETE" and not state.incomplete_allowed:
        raise ToolError(
            "INCOMPLETE is allowed only when every open needinfo on the bug is on the "
            "reporter, and the reporter has been quiet for the stale period. A reminder "
            "the reporter set, an undated needinfo, or a developer or expert reporter "
            "also rules it out. This bug does not qualify, so leave it open"
        )
    if reason := close_verdict_refusal(close_verdicts, resolution, dupe_of):
        raise ToolError(reason)


def _additions(field_name: str, value: object) -> list:
    """The items of an add-only change, `{"add": [...]}`; anything else is refused."""
    if not isinstance(value, dict) or set(value) != {"add"}:
        raise ToolError(f'{field_name} may only be added to: {{"add": [...]}}')
    items = value["add"]
    if not isinstance(items, list) or not items:
        raise ToolError(f"{field_name}.add must be a non-empty list")
    return items


def _check_add_only(
    field_name: str, value: object, current: object, allowed: Collection, what: str
) -> None:
    present = set(current or ())
    for item in _additions(field_name, value):
        if item not in allowed or isinstance(item, bool):
            raise ToolError(f"{field_name} may only add {what}, not {item!r}")
        if item in present:
            raise ToolError(f"the bug already has {item!r} in {field_name}")


def _check_regressed_by(value: object, state: TriageState, bug_id: int) -> None:
    current = set(state.bug.get("regressed_by") or ())
    for item in _additions("regressed_by", value):
        if not is_bug_id(item) or item == bug_id:
            raise ToolError(f"regressed_by may only add another bug's id, not {item!r}")
        if item in current:
            raise ToolError(f"the bug is already regressed by {item}")
        if item in set(state.bug.get("regressions") or ()):
            raise ToolError(
                f"bug {item} is one this bug regressed; Bugzilla refuses the loop"
            )


_BMO_BUG_URL = re.compile(
    r"https?://bugzilla\.mozilla\.org/show_bug\.cgi\?id=(\d+)(?:[&#].*)?",
    re.IGNORECASE,
)


def see_also_bug_ids(urls: object) -> set[int]:
    """The ids of the BMO bugs in a see_also list, which Bugzilla stores as URLs."""
    found = (_BMO_BUG_URL.fullmatch(u) for u in urls or () if isinstance(u, str))
    return {int(m.group(1)) for m in found if m}


def _check_see_also(value: object, state: TriageState, bug_id: int) -> None:
    current = see_also_bug_ids(state.bug.get("see_also"))
    for item in _additions("see_also", value):
        if not is_bug_id(item) or item == bug_id:
            raise ToolError(f"see_also may only add another bug's id, not {item!r}")
        if item in current:
            raise ToolError(f"the bug already has bug {item} in see_also")


def with_fix_tags(current: str, tags: list[str]) -> str:
    """``current`` with ``tags`` at its end; the text already there stays as it is."""
    added = " ".join(tags)
    if not current:
        return added
    return current + added if current[-1].isspace() else f"{current} {added}"


def _check_whiteboard(changes: dict, state: TriageState) -> None:
    """Turn ``{"add": [<fix tag>]}`` into the bug's full new whiteboard text."""
    fix_tags = TERMS.whiteboard.fix_tags
    easy_fix, gfb = fix_tags
    if (RESOLUTION_FIELDS | MOVE_FIELDS) & set(changes):
        raise ToolError(
            "a fix tag goes only on an analyzed bug; a close or a move is not analyzed"
        )
    if not isinstance(state.bug.get("whiteboard"), str):
        raise ToolError(
            "the bug's whiteboard was not read, so a tag could erase it; add no fix tag"
        )
    current = state.whiteboard
    present = current.casefold()
    tags = _additions("whiteboard", changes["whiteboard"])
    for tag in tags:
        if tag not in fix_tags:
            raise ToolError(
                f"whiteboard may only add {easy_fix} or {gfb}, not {tag!r}; never "
                "remove or edit the text already there"
            )
        if tag.casefold() in present:
            raise ToolError(f"the bug's whiteboard already has {tag}")
    if len(set(tags)) != len(tags):
        raise ToolError("add each tag once")
    if gfb in tags and easy_fix not in tags and easy_fix.casefold() not in present:
        raise ToolError(f"{gfb} needs {easy_fix}; add both")
    changes["whiteboard"] = with_fix_tags(current, [t for t in fix_tags if t in tags])


def _same_name(a: object, b: object) -> bool:
    return (
        isinstance(a, str)
        and isinstance(b, str)
        and a.strip().casefold() == b.strip().casefold()
    )


def _check_move(changes: dict, state: TriageState) -> None:
    component = changes.get("component")
    if not isinstance(component, str) or not component.strip():
        raise ToolError("a move names the target component; product alone is not one")
    product = changes.get("product", state.bug.get("product"))
    if not isinstance(product, str) or not product.strip():
        raise ToolError("a move names the target product as text")
    bug = state.bug
    if _same_name(product, bug.get("product")) and _same_name(
        component, bug.get("component")
    ):
        raise ToolError(
            "the target is the bug's current component; name where the bug should go"
        )
    changes["component"] = component.strip()
    if "product" in changes:
        changes["product"] = product.strip()
    if not _same_name(product, bug.get("product")):
        changes.update(CROSS_PRODUCT_DEFAULTS)


_NO_TEAM_META = (
    "this run has no team, so no meta bug applies; leave blocks out and change "
    "nothing on another bug"
)


def _check_duplicate_target(
    recorder: ActionsRecorder, params: dict, bug_id: int
) -> None:
    """The one change allowed on another bug: the team meta bug on the duplicate target.

    The duplicate is closed, so the meta bug belongs on the bug that stays open.
    """
    if TEAM is None:
        raise ToolError(_NO_TEAM_META)
    target = params.get("bug_id")
    if not any(
        u.changes.get("resolution") == "DUPLICATE"
        and u.changes.get("dupe_of") == target
        for u in updates(recorder.actions, bug_id)
    ):
        raise ToolError(
            f"you are triaging bug {bug_id}; on another bug you may only add the team "
            "meta bug to the bug this one duplicates, after the DUPLICATE change is "
            "recorded"
        )
    if updates(recorder.actions, target):
        raise ToolError(f"a change on bug {target} is already recorded")
    changes = params.get("changes")
    if not isinstance(changes, dict) or set(changes) != {"blocks"}:
        raise ToolError(f"on bug {target}, change only blocks")
    if len(_additions("blocks", changes["blocks"])) != 1:
        raise ToolError(f"add one meta bug to bug {target}")
    _check_add_only(
        "blocks",
        changes["blocks"],
        (),
        TEAM_META_BUGS,
        "a meta bug from the team's meta-bugs.md",
    )


def update_bug_hook(
    recorder: ActionsRecorder,
    bug_id: int,
    state: TriageState,
    meta_candidates: Set[int],
    close_verdicts: CloseVerdicts,
    commit_authors: Set[str],
    investigations: Investigations,
) -> ActionHook:
    """Refuse a ``bugzilla.update_bug`` outside what the plan allows.

    ``meta_candidates`` are the duplicate candidates known to be meta bugs. The
    concept search adds to it during the run, so it is read at each check.
    ``close_verdicts`` is what `record_close_verdict` stores, read the same way. It
    keeps every refused update that tries to close the triaged bug, whichever check
    refuses it. ``commit_authors`` is what `authors.commit_hook` and
    `authors.assignee_hook` collect, read the same way, and so is
    ``investigations``, which `investigations.spawn_counter` fills. An update with a
    missing or malformed bug id counts as one on the triaged bug.
    """

    def hook(action: dict) -> None:
        params = action_params(action)
        changes = params.get("changes")
        other_bug = is_bug_id(params.get("bug_id")) and params.get("bug_id") != bug_id
        closes = (
            not other_bug
            and isinstance(changes, dict)
            and bool(RESOLUTION_FIELDS & set(changes))
        )
        attempt = (
            (changes.get("resolution"), changes.get("dupe_of")) if closes else None
        )
        try:
            check(action)
        except ToolError:
            if attempt is not None:
                close_verdicts.refused.append(attempt)
            raise

    def check(action: dict) -> None:
        params = action_params(action)
        if params.get("bug_id") != bug_id and is_bug_id(params.get("bug_id")):
            _check_duplicate_target(recorder, params, bug_id)
            return
        _check_target_bug(params, bug_id)
        if not _recorded(recorder, "bugzilla.add_comment"):
            raise ToolError(
                "record the comment first; a field change travels with the comment and "
                "Bugzilla rejects a severity change without one"
            )
        if updates(recorder.actions, bug_id):
            raise ToolError(
                "a field change is already recorded; record every field change in one "
                "bugzilla_update_bug call, since a second one would replace the first's "
                "flags or blocks"
            )
        changes = params.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ToolError("changes must be a non-empty object of field -> value")
        refused = sorted(set(changes) - ALLOWED_UPDATE_FIELDS)
        if refused:
            raise ToolError(
                f"this agent may not change {', '.join(refused)}; it may change "
                f"{', '.join(sorted(ALLOWED_UPDATE_FIELDS))}. Suggest anything else in "
                "the comment."
            )
        if "severity" in changes:
            changes["severity"] = _check_severity(
                changes["severity"], state.bug.get("severity"), state.bug.get("type")
            )
        if "priority" in changes:
            changes["priority"] = _check_priority(
                changes["priority"], state.bug.get("priority")
            )
        moving = bool(MOVE_FIELDS & set(changes))
        if moving:
            _check_move(changes, state)
        if "flags" in changes:
            _check_flags(changes, state, moving, commit_authors)
        if RESOLUTION_FIELDS & set(changes):
            _check_closing(changes, state, bug_id, meta_candidates, close_verdicts)
            if changes.get("resolution") == "DUPLICATE" and "blocks" in changes:
                raise ToolError(
                    "a duplicate does not block a meta bug; add it to bug "
                    f"{changes['dupe_of']} in a second update"
                )
        if "blocks" in changes:
            if TEAM is None:
                raise ToolError(_NO_TEAM_META)
            if len(_additions("blocks", changes["blocks"])) > MAX_META_BUGS:
                raise ToolError(
                    f"add at most {MAX_META_BUGS} meta bugs; pick the ones that fit"
                )
            _check_add_only(
                "blocks",
                changes["blocks"],
                state.bug.get("blocks"),
                TEAM_META_BUGS,
                "a meta bug from the team's meta-bugs.md",
            )
        if "keywords" in changes:
            _check_add_only(
                "keywords",
                changes["keywords"],
                state.bug.get("keywords"),
                TERMS.keywords.add,
                ", ".join(TERMS.keywords.add),
            )
        if "regressed_by" in changes:
            _check_regressed_by(changes["regressed_by"], state, bug_id)
        if "see_also" in changes:
            _check_see_also(changes["see_also"], state, bug_id)
        if "whiteboard" in changes:
            _check_whiteboard(changes, state)
        if "flags" in changes:
            _check_reporter_ask(changes, state, investigations)
        for flag in changes.get("flags", ()):
            flag["new"] = True

    return hook


def install_hooks(
    recorder: ActionsRecorder,
    bug_id: int,
    state: TriageState,
    *,
    searchfox_rev: str | None,
    repo: Path,
    close_verdicts: CloseVerdicts,
    commit_authors: Set[str],
    investigations: Investigations,
    meta_candidates: Set[int] = frozenset(),
) -> None:
    """Register every hook on ``recorder``, in the order they must run.

    The comment hook refuses first. The placeholder hook then expands the blocks the
    style hook exempts, and the capture hook checks the capture steps. The link and
    style hooks check the result, and the permalink hook expands source paths. The
    marker hook runs last, once every check passed, so a close verdict knows whether it
    came before the comment.
    """
    recorder.add_hook("bugzilla.add_comment", add_comment_hook(recorder, bug_id))
    recorder.add_hook("bugzilla.add_comment", block_placeholder_hook)
    recorder.add_hook("bugzilla.add_comment", capture_block_hook)
    recorder.add_hook("bugzilla.add_comment", link_hook)
    recorder.add_hook("bugzilla.add_comment", comment_style_hook)
    recorder.add_hook("bugzilla.add_comment", refuted_close_hook(close_verdicts))
    recorder.add_hook(
        "bugzilla.add_comment", permalink_hook(permalink_prefix(searchfox_rev), repo)
    )
    recorder.add_hook("bugzilla.add_comment", comment_marker_hook(close_verdicts))
    recorder.add_hook(
        "bugzilla.update_bug",
        update_bug_hook(
            recorder,
            bug_id,
            state,
            meta_candidates,
            close_verdicts,
            commit_authors,
            investigations,
        ),
    )
