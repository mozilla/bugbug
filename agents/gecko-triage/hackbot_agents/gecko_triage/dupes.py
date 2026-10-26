"""Duplicate search in code, before the model starts and as a tool for the hunter.

Two parts run before the model starts. Each part searches the team's components, or the
bug's own component when there is no team. The newest bugs come first, and nothing
limits the status or the age.

- The crash signature search. It searches each signature of the bug as a substring of
  `cf_crash_signature`, and keeps the bugs that have the same signature. A signature
  that `SIGNATURE_CAP` bugs or more have is too generic and is dropped. A signature
  match is the strongest lead, so it comes first.
- The summary word search. It searches each content word of the summary, stemmed, as a
  whole-word prefix of the summary. A word with `WORD_CAP` matches or more is too
  common, so the search drops it. The search keeps the `MAX_WORDS` rarest words. Each
  word that a bug matches adds a weight, and a rarer word adds more.

The model sees the best leads in the prompt. The duplicate hunter then calls the
`search_concepts` tool. The hunter gives 2 to 4 concepts. Each concept is a list of
synonym stems for one aspect of the symptom. The tool searches each pair of concepts,
and all of them together, in the summaries and in the comments. A bug that matches more
concepts ranks higher. The tool marks meta bugs, and the record hooks refuse them as a
duplicate target.

The searches use the broker's boolean charts. The tool accepts only letters and digits
in a stem, so the text of a stem cannot change the query.
"""

import asyncio
import itertools
import math
import re
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated

from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolDefinition, ToolError
from pydantic import Field

from .broker_client import broker_session
from .config import TEAM
from .preflight import is_meta
from .teamconfig import scope_for

# `keywords` and `summary` let `meta_candidates` tell a meta bug.
CANDIDATE_FIELDS = "id,summary,status,resolution,dupe_of,keywords,cf_crash_signature"
SUMMARY_FIELD = "short_desc"
COMMENT_FIELD = "longdesc"
SIGNATURE_FIELD = "cf_crash_signature"
NEWEST_FIRST = "bug_id DESC"

_WORD = re.compile(r"[a-z][a-z0-9]+")
_STOPWORDS = frozenset(
    [
        "a",
        "about",
        "after",
        "again",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "before",
        "being",
        "but",
        "by",
        "can",
        "cannot",
        "could",
        "did",
        "does",
        "doesn",
        "don",
        "during",
        "each",
        "for",
        "from",
        "get",
        "gets",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "like",
        "may",
        "more",
        "most",
        "much",
        "never",
        "no",
        "not",
        "now",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "our",
        "out",
        "over",
        "same",
        "should",
        "since",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "way",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "why",
        "will",
        "with",
        "without",
        "would",
        "you",
        "your",
        "firefox",
        "mozilla",
        "browser",
        "nightly",
        "beta",
        "release",
        "version",
        "bug",
        "issue",
        "problem",
        "work",
        "works",
        "working",
    ]
)
_SUFFIXES = ("ing", "ed", "es", "s")
# Three letters keeps media terms such as av1, mp4, hdr and eme.
MIN_WORD = 3
MIN_STEM = 4
# The summary words searched, and the rarest of them that are kept.
MAX_QUERY_WORDS = 12
MAX_WORDS = 8
# A word with this many summary matches is too common to weigh, so it is dropped.
WORD_CAP = 300
# The broker drops restricted bugs from an answer, so a word query asks for more than
# WORD_CAP. A common word then still reaches WORD_CAP after the drop.
WORD_QUERY_LIMIT = WORD_CAP + 100
WEIGHT_FLOOR = 5
SEARCH_LIMIT = 50
MAX_CANDIDATES = 10
MAX_SIGNATURES = 4
MAX_SIGNATURE_CANDIDATES = 5
# A signature that this many bugs have exactly, such as `OOM | small`, is too generic
# to be a lead.
SIGNATURE_CAP = 10
# A substring query also finds longer signatures, so it asks for more bugs than it keeps.
SIGNATURE_QUERY_LIMIT = 200
# The budget of the summary and signature queries, inside the overall timeout.
WORD_BUDGET = 45
SEARCH_TIMEOUT = 60

MIN_CONCEPTS = 2
MAX_CONCEPTS = 4
MAX_SYNONYMS = 8
MIN_CONCEPT_STEM = 3
MAX_CONCEPT_STEM = 24
_CONCEPT_STEM = re.compile(rf"[a-z0-9]{{{MIN_CONCEPT_STEM},{MAX_CONCEPT_STEM}}}")
# A comment search takes 5 to 20 seconds on bugzilla.mozilla.org.
CONCEPT_BUDGET = 30
CONNECT_GRACE = 10
MAX_CONCEPT_RESULTS = 10

SERVER_NAME = "dupes"
SEARCH_CONCEPTS_TOOL = f"mcp__{SERVER_NAME}__search_concepts"

_SIGNATURE = re.compile(r"\[@\s*([^\]]*?)\s*\]")
_WHERE = {SUMMARY_FIELD: "summary", COMMENT_FIELD: "comments"}


def _log(message: str) -> None:
    print(f"[gecko_triage] {message}", file=sys.stderr)


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= MIN_STEM:
            return word[: -len(suffix)]
    return word


def content_words(summary: str) -> list[str]:
    """The summary's content words, stemmed, in order, without repeats."""
    words: list[str] = []
    for token in _WORD.findall((summary or "").lower()):
        if len(token) < MIN_WORD or token in _STOPWORDS:
            continue
        stem = _stem(token)
        if stem in _STOPWORDS or stem in words:
            continue
        words.append(stem)
    return words[:MAX_QUERY_WORDS]


def crash_signatures(text: str | None, limit: int | None = MAX_SIGNATURES) -> list[str]:
    """The signatures in a `cf_crash_signature` value, without the `[@ ]` marks."""
    found = [s for s in _SIGNATURE.findall(text or "") if s]
    return list(dict.fromkeys(found))[:limit]


def _any_word(stems: list[str]) -> str:
    """A pattern for a word that starts with one of ``stems``."""
    return rf"\b({'|'.join(stems)})"


def _query(
    products: list[str],
    components: list[str],
    charts: list[tuple[str, str, object]],
    limit: int = SEARCH_LIMIT,
) -> dict:
    """Search params: the scope, one chart for each (field, operator, value), newest first."""
    params: dict = {"product": products, "component": components}
    for n, (chart_field, op, value) in enumerate(charts, start=1):
        params |= {f"f{n}": chart_field, f"o{n}": op, f"v{n}": value}
    return params | {
        "include_fields": CANDIDATE_FIELDS,
        "order": NEWEST_FIRST,
        "limit": limit,
    }


def word_query(products: list[str], components: list[str], word: str) -> dict:
    return _query(
        products,
        components,
        [(SUMMARY_FIELD, "regexp", _any_word([word]))],
        WORD_QUERY_LIMIT,
    )


def signature_query(products: list[str], components: list[str], sig: str) -> dict:
    return _query(
        products,
        components,
        [(SIGNATURE_FIELD, "substring", sig)],
        SIGNATURE_QUERY_LIMIT,
    )


def rarest_words(hits: dict[str, list[dict]]) -> list[str]:
    """The ``MAX_WORDS`` rarest words under ``WORD_CAP`` matches, in summary order."""
    known = [w for w, bugs in hits.items() if len(bugs) < WORD_CAP]
    rarest = set(sorted(known, key=lambda w: len(hits[w]))[:MAX_WORDS])
    return [w for w in known if w in rarest]


def word_weight(count: int) -> float:
    """The weight of a word with ``count`` summary matches.

    The weight falls with the square root of the count, so a rare word counts more.
    Below ``WEIGHT_FLOOR`` the weight stays the same, so a typo or a device name with
    one match does not outweigh two symptom words.
    """
    return 1 / math.sqrt(max(count, WEIGHT_FLOOR))


def rank_candidates(
    hits: dict[str, list[dict]], bug_id: int, limit: int = MAX_CANDIDATES
) -> list[dict]:
    """The best leads first, without ``bug_id``.

    Each of the rarest words a bug matches adds ``word_weight``. So two words count
    more than one word of the same rarity. One rare symptom word ("fade", 7 matches)
    still counts more than two common ones ("bluetooth" 72 and "headphone" 52).
    """
    found: dict[int, dict] = {}
    for word in rarest_words(hits):
        weight = word_weight(len(hits[word]))
        for bug in hits[word]:
            if bug.get("id") == bug_id:
                continue
            entry = found.setdefault(bug["id"], {**bug, "words": [], "score": 0.0})
            if word not in entry["words"]:
                entry["words"].append(word)
                entry["score"] += weight
    ranked = sorted(found.values(), key=lambda b: (-b["score"], -b["id"]))
    return ranked[:limit]


def with_signature_matches(
    sig_hits: dict[str, list[dict]],
    ranked: list[dict],
    bug_id: int,
    limit: int = MAX_CANDIDATES,
) -> list[dict]:
    """The crash signature matches, newest first, then the word matches.

    The query is a substring search, so only a bug that has the same signature counts.
    A signature that ``SIGNATURE_CAP`` bugs or more have is too generic to count.
    """
    words = {c["id"]: c for c in ranked}
    matches: dict[int, dict] = {}
    for sig, bugs in sig_hits.items():
        same = [
            b
            for b in bugs
            if b.get("id") != bug_id
            and sig in crash_signatures(b.get(SIGNATURE_FIELD), limit=None)
        ]
        if len(same) >= SIGNATURE_CAP:
            continue
        for bug in same:
            entry = matches.setdefault(
                bug["id"],
                {**bug, "words": words.get(bug["id"], {}).get("words", [])},
            )
            entry.setdefault("signatures", []).append(sig)
    first = sorted(matches.values(), key=lambda b: -b["id"])[:MAX_SIGNATURE_CANDIDATES]
    shown = {c["id"] for c in first}
    return (first + [c for c in ranked if c["id"] not in shown])[:limit]


def meta_candidates(candidates: list[dict]) -> frozenset[int]:
    """The ids of the candidates that are meta bugs, which a bug never duplicates."""
    return frozenset(c["id"] for c in candidates if is_meta(c))


def _status(c: dict) -> str:
    return " ".join(filter(None, (c.get("status", "?"), c.get("resolution"))))


def _matches(c: dict) -> str:
    parts = [f"crash signature [@ {s}]" for s in c.get("signatures", ())]
    if c.get("words"):
        parts.append(f"words: {', '.join(c['words'])}")
    return "; ".join(parts)


def render_candidates(candidates: list[dict]) -> str:
    if not candidates:
        return "(none found by the crash signature and summary word search)"
    return "\n".join(
        f"- bug {c['id']} ({_status(c)}): {c.get('summary', '')} [{_matches(c)}]"
        for c in candidates
    )


async def gather_within(coros: list, budget: float) -> list:
    """Each awaitable's result, or None when it failed or did not end in ``budget`` s.

    Never raises.
    """
    tasks = [asyncio.ensure_future(c) for c in coros]
    if not tasks:
        return []
    try:
        _, pending = await asyncio.wait(tasks, timeout=budget)
    finally:
        # Also when an outer timeout cancels the wait, so no search outlives the session.
        pending = [t for t in tasks if not t.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    results = []
    for task in tasks:
        if task.cancelled():
            results.append(None)
        elif (e := task.exception()) is not None:
            _log(f"a duplicate search query failed ({e})")
            results.append(None)
        else:
            results.append(task.result())
    return results


@asynccontextmanager
async def broker_search(url: str, timeout: float):
    """A ``search(params)`` on the broker's `search_bugs`, over one MCP session.

    The session takes calls in parallel, and ends after ``timeout`` seconds.
    """
    async with broker_session(url, timeout) as call:

        async def search(params: dict) -> list[dict]:
            return (await call("search_bugs", {"params": params})).get("bugs") or []

        yield search


def search_scope(bug: dict) -> tuple[list[str], list[str]]:
    """The products and the components the search covers: the run's scope for ``bug``."""
    scope = scope_for(TEAM, bug)
    return sorted({c.product for c in scope}), [c.component for c in scope]


async def find_candidates(bugzilla_mcp_server: dict, bug: dict) -> list[dict]:
    """The crash signature and summary word leads for ``bug``.

    Never raises: [] on any failure. It gives up after SEARCH_TIMEOUT seconds, so a
    hung broker cannot block the run.
    """
    try:
        return await _find_candidates(bugzilla_mcp_server.get("url"), bug)
    except Exception as e:  # see docstring
        _log(f"duplicate search failed ({e})")
        return []


async def _find_candidates(url: str | None, bug: dict) -> list[dict]:
    words = content_words(bug.get("summary", ""))
    sigs = crash_signatures(bug.get(SIGNATURE_FIELD))
    if not url or not (words or sigs):
        return []
    products, components = search_scope(bug)
    queries = [word_query(products, components, w) for w in words]
    queries += [signature_query(products, components, s) for s in sigs]
    async with broker_search(url, SEARCH_TIMEOUT) as search:
        answers = await gather_within([search(q) for q in queries], WORD_BUDGET)
    word_hits = {w: a for w, a in zip(words, answers) if a is not None}
    sig_hits = {s: a for s, a in zip(sigs, answers[len(words) :]) if a is not None}
    bug_id = bug.get("id", 0)
    return with_signature_matches(sig_hits, rank_candidates(word_hits, bug_id), bug_id)


def validate_concepts(concepts: object) -> list[list[str]]:
    """``concepts`` in lower case without repeats. Raises ValueError on a bad one."""
    if not isinstance(concepts, list) or not (
        MIN_CONCEPTS <= len(concepts) <= MAX_CONCEPTS
    ):
        raise ValueError(f"give {MIN_CONCEPTS} to {MAX_CONCEPTS} concepts")
    groups = []
    for concept in concepts:
        if not isinstance(concept, list) or not 1 <= len(concept) <= MAX_SYNONYMS:
            raise ValueError(f"give each concept 1 to {MAX_SYNONYMS} stems")
        stems = []
        for stem in concept:
            low = stem.lower() if isinstance(stem, str) else ""
            if not _CONCEPT_STEM.fullmatch(low):
                raise ValueError(
                    f"stem {stem!r}: use {MIN_CONCEPT_STEM} to {MAX_CONCEPT_STEM} "
                    "letters or digits, with no space or symbol"
                )
            if low not in stems:
                stems.append(low)
        groups.append(stems)
    return groups


def concept_groups(count: int) -> list[tuple[int, ...]]:
    """Each pair of concept indexes, then all of them when there are more than two."""
    pairs = list(itertools.combinations(range(count), 2))
    return pairs + ([tuple(range(count))] if count > 2 else [])


def concept_queries(
    products: list[str], components: list[str], bug_id: int, concepts: list[list[str]]
) -> list[tuple[tuple[int, ...], str, dict]]:
    """(concept indexes, field, params) for each query of a concept search."""
    queries = []
    for chart_field in (SUMMARY_FIELD, COMMENT_FIELD):
        for groups in concept_groups(len(concepts)):
            charts = [(chart_field, "regexp", _any_word(concepts[g])) for g in groups]
            charts.append(("bug_id", "notequals", bug_id))
            queries.append((groups, chart_field, _query(products, components, charts)))
    return queries


def rank_concept_hits(
    concepts: list[list[str]],
    answers: list[tuple[tuple[int, ...], str, list[dict] | None]],
    bug_id: int,
    limit: int = MAX_CONCEPT_RESULTS,
) -> list[dict]:
    """The bugs found, best first, without ``bug_id``.

    A bug that matches more concepts ranks higher, then one that matches more of them
    in its summary, then the newest. Meta bugs come last.
    """
    found: dict[int, dict] = {}
    for groups, chart_field, bugs in answers:
        for bug in bugs or ():
            if bug.get("id") == bug_id:
                continue
            entry = found.setdefault(
                bug["id"],
                {"bug": bug, "groups": set(), "summary": set(), "where": set()},
            )
            entry["groups"].update(groups)
            entry["where"].add(chart_field)
            if chart_field == SUMMARY_FIELD:
                entry["summary"].update(groups)
    ranked = sorted(
        found.values(),
        key=lambda e: (
            is_meta(e["bug"]),
            -len(e["groups"]),
            -len(e["summary"]),
            -e["bug"]["id"],
        ),
    )
    return [
        {
            "id": e["bug"]["id"],
            "summary": e["bug"].get("summary"),
            "status": e["bug"].get("status"),
            "resolution": e["bug"].get("resolution"),
            "dupe_of": e["bug"].get("dupe_of"),
            "matched": [concepts[g][0] for g in sorted(e["groups"])],
            "where": [
                _WHERE[f] for f in (SUMMARY_FIELD, COMMENT_FIELD) if f in e["where"]
            ],
            "meta": is_meta(e["bug"]),
        }
        for e in ranked[:limit]
    ]


@dataclass
class ConceptContext:
    """What `search_concepts` is bound to: the broker, the triaged bug and its scope.

    ``meta_ids`` is the set the record hooks read, so a meta bug the tool finds is
    refused as a duplicate target.
    """

    url: str
    bug_id: int
    products: list[str]
    components: list[str]
    meta_ids: set[int] = field(default_factory=set)


async def run_concept_search(search, ctx: ConceptContext, concepts: list[list[str]]):
    """Run every query of a concept search within ``CONCEPT_BUDGET``. Never raises."""
    queries = concept_queries(ctx.products, ctx.components, ctx.bug_id, concepts)
    answers = await gather_within([search(q) for _, _, q in queries], CONCEPT_BUDGET)
    ranked = rank_concept_hits(
        concepts,
        [(g, f, a) for (g, f, _), a in zip(queries, answers)],
        ctx.bug_id,
    )
    ctx.meta_ids.update(c["id"] for c in ranked if c["meta"])
    result: dict = {
        "searched": len(queries),
        "answered": sum(a is not None for a in answers),
        "candidates": ranked,
    }
    if (missed := result["searched"] - result["answered"]) > 0:
        result["note"] = (
            f"{missed} of {len(queries)} queries failed or ran out of time, so a "
            "match can be missing"
        )
    return result


async def _search_concepts(ctx: ConceptContext, concepts: list[list[str]]) -> dict:
    async with broker_search(ctx.url, CONCEPT_BUDGET + CONNECT_GRACE) as search:
        return await run_concept_search(search, ctx, concepts)


async def search_concepts_tool(
    ctx: ConceptContext,
    concepts: Annotated[
        list[list[str]],
        Field(
            description=(
                f"{MIN_CONCEPTS} to {MAX_CONCEPTS} concepts. Each concept is a list of "
                f"1 to {MAX_SYNONYMS} synonym stems for one aspect of the symptom, "
                f'such as ["fade", "fading", "ramp"]. A stem is '
                f"{MIN_CONCEPT_STEM} to {MAX_CONCEPT_STEM} letters or digits, and it "
                "matches a word that starts with it."
            )
        ),
    ],
) -> dict:
    try:
        groups = validate_concepts(concepts)
    except ValueError as e:
        raise ToolError(
            str(e), payload={"error": "invalid_concepts", "reason": str(e)}
        ) from None
    try:
        return await _search_concepts(ctx, groups)
    except Exception as e:  # the hunter can still search by hand
        _log(f"concept search failed ({e})")
        return {"candidates": [], "note": f"the search failed: {e}"}


SEARCH_CONCEPTS = ToolDefinition(
    name="search_concepts",
    namespace=SERVER_NAME,
    description=f"""Search for earlier reports of the bug being triaged, by concept.

Give {MIN_CONCEPTS} to {MAX_CONCEPTS} concepts. Each concept is one aspect of the
symptom, as a list of synonym stems. The tool searches the summaries and the comment
text of the bugs in scope. Each query joins two concepts, or all of them, with AND, and
a concept matches when any of its stems starts a word. The newest bugs come first. The
triaged bug is left out.

Returns up to {MAX_CONCEPT_RESULTS} bugs. A bug that matches more concepts comes first,
then a match in the summary, then the newest. Each bug has its status, resolution and
dupe_of, the concepts it matched (by the first stem), where they matched, and `meta`.
Never name a bug with `meta: true` as the duplicate.""",
    handler=search_concepts_tool,
)


def concepts_server(ctx: ConceptContext):
    """The in-process MCP server with ``search_concepts``, bound to ``ctx``."""
    return build_sdk_server(SERVER_NAME, ctx, [SEARCH_CONCEPTS])
