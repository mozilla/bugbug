"""Knowledge from earlier bugs: crash bugs fixed before the cutoff, with their fixes.

Retrieval for the agent (RAG over Mozilla's own history). Candidates come from
Bugzilla searches limited to bugs whose resolution changed to FIXED before the
cutoff: by crash signature, by the crashing functions named in their summary,
by the agent's query, and by component. They are ranked by what they share with
the current bug (signature, crashing functions, component, words), and the best
are read as they were at the cutoff: the report, the discussion that led to the
fix, and the fix itself, the landing changeset, which is refused if it was pushed
after the cutoff.

An earlier bug is evidence about another bug: it supports an inference about the
current one by analogy, never an established fact (see ``react_tools``).
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .extraction import interesting_frames
from .models import CrashInformationState
from .prompting import clip, one_line
from .sources import LeakageBlocked, NotFound, SourceError, Sources
from .sources.bugzilla import Comment, parse_time
from .sources.code import MAX_DIFF_CHARS
from .sources.hgmo import HgmoClient
from .textutil import jaccard

PRIOR_FIELDS = (
    "id,summary,product,component,cf_crash_signature,creation_time,cf_last_resolved"
)
EARLIEST = "2005-01-01"
# A case must share content with the current bug (a signature, a crashing
# function, distinctive words), not just the component and recency (2.0).
MIN_SCORE = 2.5
# Code changes over the years: recently fixed bugs are the better analogies.
RECENT = timedelta(days=3 * 365)
# A changeset link in a landing comment: (repository, node). Uplifts to release
# branches are skipped: the change itself landed first on the main repositories.
_REV = re.compile(
    r"https://hg\.mozilla\.org/((?:integration/)?[\w-]+)/rev/([0-9a-f]{12,40})"
)
_URL = re.compile(r"https?://\S+")
# Summary words too common in crash bugs to find related ones.
_GENERIC = frozenset(
    "crash crashes crashing crashed when with after before during while from into "
    "this that mostly spike startup shutdown assertion failure failed nightly beta "
    "release intermittent permanent frequent sometimes always only firefox".split()
)
# A class name in a qualified name (namespaces are lowercase in Gecko).
_CLASS = re.compile(r"^(?:ns|moz)?[A-Z][A-Za-z0-9]{4,}$")
# Signatures that say nothing about the code: unsymbolized or generic frames.
_UNINFORMATIVE = re.compile(r"^(\?\?|@0x[0-9a-f]+|EMPTY.*|OOM \| \w+)$", re.I)
# Capitalized words that are not code.
_NOT_CODE = {
    "JavaScript",
    "WebAssembly",
    "TypeScript",
    "GitHub",
    "macOS",
    "iOS",
    "WebGL",
    "WebGPU",
    "WebRTC",
}
_CODEISH = re.compile(r"\b(?:[A-Za-z_]\w*::)*~?[A-Za-z_]\w*\b")
# Comments that say nothing about the bug: bots and attachment notices (the fix's
# title is given with the fix).
_NOISE = re.compile(
    r"^Created attachment \d+|auto_?nag|is linked to a topcrash signature|"
    r"The severity field|^Pushed by ",
    re.I,
)
_FRAMES = re.compile(r"(Top \d+ frames[^\n]*\n```\n)(.*?)(\n```)", re.S)


def _fewer_frames(report: str, keep: int = 5) -> str:
    """A report's "Top 10 frames" block cut to its first frames."""
    return _FRAMES.sub(
        lambda m: m.group(1) + "\n".join(m.group(2).splitlines()[:keep]) + m.group(3),
        report,
    )


def _diff_only(patch: str) -> str:
    """A changeset's patch from its first diff: its header (author, date, node,
    review link) and commit message repeat what the headline gives."""
    start = patch.find("diff --git")
    return patch[start:] if start > 0 else patch


@dataclass
class PriorCase:
    bug_id: int
    summary: str
    product: str
    component: str
    signatures: list[str]
    created: datetime
    fixed: datetime
    report: str
    discussion: list[str] = field(default_factory=list)
    fix_title: str = ""
    fix: dict[str, Any] | None = None
    score: float = 0.0

    def headline(self) -> str:
        fix = f"; fix: {self.fix_title}" if self.fix_title else ""
        return f"earlier bug {self.bug_id} (fixed {self.fixed:%Y-%m-%d}): {self.summary}{fix}"

    def text(self) -> str:
        lines = [
            f"Bug {self.bug_id} [{self.product} :: {self.component}]: {self.summary}",
            f"Filed {self.created:%Y-%m-%d}; fixed before the cutoff ({self.fixed:%Y-%m-%d}).",
        ]
        if self.signatures:
            lines.append("Signatures: " + " | ".join(self.signatures))
        if self.report:
            lines.append(f"Report: {clip(_fewer_frames(self.report), 1_200)}")
        said = [text for text in self.discussion if not _NOISE.search(text)]
        if said:
            lines.append("Discussion before the fix:")
            lines += [f"- {one_line(text, 500)}" for text in said]
        if self.fix:
            first = (self.fix.get("desc") or "").splitlines()
            lines.append(
                f"Fix: changeset {self.fix['node'][:12]} (pushed {(self.fix.get('pushdate') or '')[:10]}): "
                + (first[0] if first else self.fix_title)
            )
            files = [f for f in self.fix.get("files") or [] if f]
            if files:
                lines.append("Files: " + ", ".join(files[:8]))
            lines.append(clip(_diff_only(self.fix.get("diff") or ""), 2_000))
        elif self.fix_title:
            lines.append(f"Fix: {self.fix_title} (changeset not readable)")
        return "\n".join(lines)


class PriorBugs:
    """Earlier bugs fixed before the cutoff that resemble the current one."""

    def __init__(self, state: CrashInformationState, sources: Sources) -> None:
        self.state = state
        self.sources = sources

    @property
    def cutoff(self) -> datetime:
        return self.state.as_of or datetime.now(UTC)

    async def find(
        self, query: str | None = None, *, limit: int = 3, skip: set[int] | None = None
    ) -> list[PriorCase]:
        skip = skip or set()
        candidates = await self._candidates(query)
        ranked = sorted(
            (
                b
                for b in candidates
                if b["id"] not in skip and self._score(b, query) >= MIN_SCORE
            ),
            key=lambda b: -self._score(b, query),
        )
        # A few spare candidates: a case without a readable report is dropped.
        reads = await asyncio.gather(
            *(self._read(bug, self._score(bug, query)) for bug in ranked[: limit + 2])
        )
        return [case for case in reads if case is not None][:limit]

    # --- Retrieval ------------------------------------------------------------------

    def _searches(self, query: str | None) -> list[dict[str, Any]]:
        snap = self.state.report_snapshot
        searches: list[dict[str, Any]] = []
        for signature in (snap.crash_signatures if snap else [])[:2]:
            fragment = signature.removeprefix("[@").removesuffix("]").split("|")[0]
            if _informative(fragment):
                searches.append(
                    {
                        "f1": "cf_crash_signature",
                        "o1": "substring",
                        "v1": fragment.strip(),
                    }
                )
        for name in [*self._frame_names()[:2], *self._classes()]:
            searches.append({"summary": name})
        # The summary's most distinctive words: alone, then together.
        words = self._distinctive()
        for terms in dict.fromkeys([words[0], " ".join(words)] if words else []):
            searches.append({"f1": "short_desc", "o1": "allwordssubstr", "v1": terms})
        if query:
            words = sorted(set(code_identifiers(query)) or set(query.split()), key=len)
            if words:
                searches.append(
                    {
                        "f1": "short_desc",
                        "o1": "allwordssubstr",
                        "v1": " ".join(words[-2:]),
                    }
                )
        if snap and snap.product and snap.component:
            searches.append(
                {
                    "product": snap.product,
                    "component": snap.component,
                    "keywords": "crash",
                }
            )
        return searches

    async def _candidates(self, query: str | None) -> list[dict[str, Any]]:
        common = {
            "resolution": "FIXED",
            "chfield": "resolution",
            "chfieldvalue": "FIXED",
            "chfieldfrom": EARLIEST,
            "chfieldto": f"{self.cutoff:%Y-%m-%d}",
            "include_fields": PRIOR_FIELDS,
            # Newest first: the 20 most recent matches are the likeliest analogies.
            "order": "bug_id DESC",
            "limit": 20,
        }
        results = await asyncio.gather(
            *(self.sources.bugzilla.search(common | s) for s in self._searches(query)),
            return_exceptions=True,
        )
        found: dict[int, dict[str, Any]] = {}
        for bugs in results:
            if isinstance(bugs, BaseException):
                continue
            for bug in bugs:
                if self._before_cutoff(bug):
                    found.setdefault(bug["id"], bug)
        return list(found.values())

    def _before_cutoff(self, bug: dict[str, Any]) -> bool:
        """Filed and last resolved before the cutoff, and not the current bug."""
        if bug.get("id") == self.state.bug_id:
            return False
        resolved = bug.get("cf_last_resolved")
        if not resolved or not bug.get("creation_time"):
            return False
        cutoff = self.cutoff
        return parse_time(bug["creation_time"]) < cutoff and _when(resolved) < cutoff

    def _distinctive(self) -> list[str]:
        """The two most distinctive words of the summary: code-like words first
        (options, identifiers, versions), then the longest."""
        snap = self.state.report_snapshot
        text = re.sub(r"\[@[^\]]*\]", " ", snap.summary if snap else "")
        words = [
            w.strip("-.:")
            for w in re.findall(r"[A-Za-z][\w:.-]*\w", text)
            if w.lower() not in _GENERIC and len(w.strip("-.:")) >= 4
        ]
        ranked = sorted(
            dict.fromkeys(words),
            key=lambda w: (not re.search(r"[-_:]|\d|[a-z][A-Z]", w), -len(w)),
        )
        return ranked[:2]

    def _classes(self) -> list[str]:
        """The classes of the crashing code, from qualified names in the signatures,
        frames and summary (``mozilla::ActiveScrolledRoot::AssertDepthInvariant``
        gives ``ActiveScrolledRoot``): earlier bugs about the same class rarely name
        the same method, and may sit in another component."""
        snap = self.state.report_snapshot
        names = [
            *(snap.crash_signatures if snap else []),
            *(f.function for f in interesting_frames(self.state.indicators)),
            snap.summary if snap else "",
        ]
        classes: list[str] = []
        for name in names:
            for qualified in re.findall(r"(?:\w+::)+\w+", name):
                owner = qualified.split("::")[-2]
                if _CLASS.match(owner) and owner not in classes:
                    classes.append(owner)
        return classes[:2]

    def _frame_names(self) -> list[str]:
        names = [
            short_symbol(f.function) for f in interesting_frames(self.state.indicators)
        ]
        return list(dict.fromkeys(n for n in names if len(n) >= 4))

    def _score(self, bug: dict[str, Any], query: str | None) -> float:
        snap = self.state.report_snapshot
        text = f"{bug.get('summary', '')} {bug.get('cf_crash_signature', '')}"
        mine = " ".join([snap.summary, *snap.crash_signatures]) if snap else ""
        score = 2.0 * jaccard(mine, text)
        theirs = {_norm(s) for s in _signatures(bug.get("cf_crash_signature", ""))}
        mine_signatures = {
            _norm(s) for s in (snap.crash_signatures if snap else []) if _informative(s)
        }
        if theirs & mine_signatures:
            score += 3.0
        lowered = text.lower()
        score += 1.5 * min(
            2, sum(1 for n in self._frame_names() if n.lower() in lowered)
        )
        score += 1.0 * sum(1 for w in self._distinctive() if w.lower() in lowered)
        if any(c.lower() in lowered for c in self._classes()):
            score += 1.5
        if snap and (bug.get("product"), bug.get("component")) == (
            snap.product,
            snap.component,
        ):
            score += 1.0
        if query:
            score += 2.0 * jaccard(query, text)
        if self.cutoff - _when(bug["cf_last_resolved"]) < RECENT:
            score += 1.0
        return score

    # --- Reading a case ---------------------------------------------------------------

    async def _read(self, bug: dict[str, Any], score: float) -> PriorCase | None:
        try:
            view = await self.sources.bugzilla.view(bug["id"], self.state.as_of)
        except SourceError:
            return None
        comments = view.comments
        landings = [c for c in comments if _is_landing(c.text)]
        first_landing = landings[0].created if landings else None
        before_fix = [
            c
            for c in comments[1:]
            if first_landing is None or c.created < first_landing
        ]
        case = PriorCase(
            bug_id=bug["id"],
            summary=bug.get("summary", ""),
            product=bug.get("product", ""),
            component=bug.get("component", ""),
            signatures=_signatures(bug.get("cf_crash_signature", "")),
            created=parse_time(bug["creation_time"]),
            fixed=first_landing or _when(bug["cf_last_resolved"]),
            report=comments[0].text if comments else "",
            discussion=[c.text for c in before_fix[-3:] if c.text.strip()],
            fix_title=_fix_title(landings),
            score=round(score, 2),
        )
        links = [
            (repo, node)
            for c in landings
            for repo, node in _REV.findall(c.text)
            if not repo.startswith("releases")
        ]
        for repo, node in dict.fromkeys(links):
            try:
                case.fix = await self._commit(repo, node)
                break
            except (LeakageBlocked, NotFound, SourceError):
                continue
        return case

    async def _commit(self, repo: str, node: str) -> dict[str, Any]:
        """A changeset of any hg.mozilla.org repository (Thunderbird's fixes land
        in comm-central), refused if it was pushed after the cutoff."""
        hgmo = self.sources.hgmo
        client = HgmoClient(hgmo.http, hgmo.base_url, repo)
        info = await client.commit(node)
        as_of = self.state.as_of
        if as_of is not None and (info["push_ts"] or 0) > as_of.timestamp():
            raise LeakageBlocked(f"changeset {node} was pushed after the cutoff")
        diff = await client.commit_diff(node)
        info["diff"] = diff[:MAX_DIFF_CHARS]
        info["url"] = client.rev_url(info["node"])
        return info


def code_identifiers(text: str) -> list[str]:
    """Tokens of a text that look like code (qualified names, camelCase, mFoo)."""
    found = []
    for token in _CODEISH.findall(text):
        if token in _NOT_CODE or len(token) < 4:
            continue
        codeish = (
            "::" in token
            or "_" in token.strip("_")
            or re.search(r"[a-z][A-Z]", token)
            or re.match(r"^[ma][A-Z]", token)
        )
        if codeish and token not in found:
            found.append(token)
    return found


def short_symbol(function: str) -> str:
    """``mozilla::dom::Foo::Bar`` -> ``Foo::Bar``: how definitions appear in source."""
    parts = [p for p in function.split("::") if p]
    return "::".join(parts[-2:]) if len(parts) > 1 else function


def _is_landing(text: str) -> bool:
    """A comment announcing that a fix landed: pulsebot's "Pushed by", or a comment
    made only of changeset links (merges, older landings). Comments that discuss a
    changeset, such as bisection results, usually name the one that caused the bug."""
    lowered = text.lower()
    if "bisect" in lowered or "bad revision" in lowered or not _REV.search(text):
        return False
    if text.startswith("Pushed by"):
        return True
    return all(_REV.search(line) for line in text.splitlines() if line.strip())


def _informative(signature: str) -> bool:
    fragment = signature.removeprefix("[@").removesuffix("]").split("|")[0].strip()
    return len(re.sub(r"[^A-Za-z]", "", fragment)) >= 4 and not _UNINFORMATIVE.match(
        fragment
    )


def _fix_title(landings: list[Comment]) -> str:
    """The commit title a landing comment quotes after its links."""
    for comment in landings:
        for line in comment.text.splitlines():
            line = _URL.sub("", line).strip()
            if line and not line.startswith("Pushed by"):
                return one_line(line, 200)
    return ""


def _signatures(raw: str) -> list[str]:
    return [s.strip() for s in re.findall(r"\[@[^\]]*\]", raw or "")]


def _norm(signature: str) -> str:
    return (
        re.sub(r"\s+", " ", signature.removeprefix("[@").removesuffix("]"))
        .strip()
        .lower()
    )


def _when(value: str) -> datetime:
    """Bugzilla's cf_last_resolved ("2026-04-25 10:00:00", UTC) or an ISO time."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(" ", "T"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
