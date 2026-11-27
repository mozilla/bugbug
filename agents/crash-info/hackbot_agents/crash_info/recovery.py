"""Run one lookup in a source and record what it found as evidence."""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .evidence import EvidenceStore
from .knowledge import PriorBugs
from .models import (
    ActionType,
    CrashInformationState,
    EvidenceSource,
    FactKind,
    KnownFact,
    Provenance,
)
from .prompting import clip, one_line
from .sources import LeakageBlocked, NotFound, SourceError, Sources
from .sources.searchfox import CodeHit

log = logging.getLogger(__name__)

MAX_ITEM_CHARS = 8_000


@dataclass
class Lookup:
    """One call to a source: the action and its arguments."""

    action: ActionType
    arguments: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Identity of the lookup, for spotting repeats regardless of the gap."""
        args = {
            k: str(v).strip().lower() for k, v in sorted(self.arguments.items()) if v
        }
        return f"{self.action.value}:{json.dumps(args, sort_keys=True)}"


@dataclass
class ActionResult:
    outcome: Literal["new_evidence", "no_new_evidence", "error", "blocked"]
    evidence_ids: list[str] = field(default_factory=list)
    note: str | None = None
    duration_s: float = 0.0
    # Items the lookup returned that were already in the inventory.
    seen_ids: list[str] = field(default_factory=list)


@dataclass
class _Item:
    kind: str
    summary: str
    content: str
    provenance: Provenance
    # Statements computed deterministically from the item (not by a model),
    # recorded as known facts (the report's "Recovered facts").
    facts: list[str] = field(default_factory=list)


class Recovery:
    def __init__(
        self,
        state: CrashInformationState,
        store: EvidenceStore,
        sources: Sources,
        crash_data: bool = True,
    ) -> None:
        self.state = state
        self.store = store
        self.sources = sources
        # Fall back on other crashes of a signature for a stack (settings'
        # investigation.crash_data).
        self.crash_data = crash_data

    async def run(self, lookup: Lookup, action_id: str) -> ActionResult:
        handlers = {
            ActionType.SEARCH_CODE: self._search_code,
            ActionType.INSPECT_REVISION: self._inspect_revision,
            ActionType.INSPECT_CRASH_EVIDENCE: self._inspect_crash_evidence,
            ActionType.SEARCH_BUGZILLA: self._search_bugzilla,
            ActionType.SEARCH_PRIOR_BUGS: self._search_prior_bugs,
        }
        started = time.monotonic()
        try:
            items = await handlers[lookup.action](lookup.arguments)
        except LeakageBlocked as e:
            return ActionResult(
                "blocked", note=str(e), duration_s=time.monotonic() - started
            )
        except NotFound as e:
            return ActionResult(
                "no_new_evidence",
                note=f"not found: {e}",
                duration_s=time.monotonic() - started,
            )
        except (SourceError, ValueError) as e:
            log.warning("%s failed: %s", lookup.action.value, e)
            return ActionResult(
                "error", note=str(e)[:300], duration_s=time.monotonic() - started
            )
        new_ids, seen_ids = [], []
        for item in items:
            evidence, is_new = self.store.add(
                item.kind,
                item.summary,
                clip(item.content, MAX_ITEM_CHARS),
                item.provenance,
                action_id,
            )
            if is_new:
                new_ids.append(evidence.id)
                for claim in item.facts:
                    self._record_fact(claim, evidence.id)
            else:
                seen_ids.append(evidence.id)
        duration = time.monotonic() - started
        if not items:
            return ActionResult(
                "no_new_evidence", note="no results", duration_s=duration
            )
        if not new_ids:
            return ActionResult(
                "no_new_evidence",
                note="only evidence already seen",
                duration_s=duration,
                seen_ids=seen_ids,
            )
        return ActionResult(
            "new_evidence", new_ids, duration_s=duration, seen_ids=seen_ids
        )

    def _record_fact(self, claim: str, evidence_id: str) -> None:
        if any(f.claim == claim for f in self.state.known_facts):
            return
        self.state.known_facts.append(
            KnownFact(
                id=self.state.next_id("fact"),
                claim=claim,
                kind=FactKind.OBSERVATION,
                category="environment",
                evidence_ids=[evidence_id],
                origin="recovered",
            )
        )

    # --- Code ------------------------------------------------------------------------

    def _code_provenance(self, locator: str, url: str | None) -> Provenance:
        code = self.sources.code
        assert code is not None
        return Provenance(
            source=code.source, locator=locator, url=url, revision=code.revision
        )

    async def _search_code(self, args: dict[str, Any]) -> list[_Item]:
        code = self.sources.code
        if code is None:
            raise SourceError("no code source configured")
        rev = code.revision[:12]
        if args.get("file"):
            line = int(args.get("line") or 0)
            focus = args.get("query") or None
            excerpt = await code.read(
                args["file"], line, context=30 if line or focus else 60, focus=focus
            )
            centre = (excerpt.start + excerpt.end) // 2
            return [
                _Item(
                    "code",
                    f"{excerpt.path} lines {excerpt.start}-{excerpt.end} at {rev}"
                    + (f" (around {focus})" if focus else ""),
                    excerpt.text,
                    self._code_provenance(
                        f"{excerpt.path}:{centre}@{rev}", excerpt.url
                    ),
                )
            ]
        query = str(args["query"])
        hits = await code.search(query, args.get("path_prefix") or None, limit=12)
        if not hits:
            return []
        listing = "\n".join(f"{h.path}:{h.line}: {one_line(h.text, 160)}" for h in hits)
        items = [
            _Item(
                "code_search",
                f"{len(hits)} matches for '{query}' at {rev}",
                listing,
                self._code_provenance(
                    f"search '{query}'@{rev}", code.url_for(hits[0].path)
                ),
            )
        ]
        # One step should show code, not just where it is: read the best hit.
        best = _best_hit(hits)
        try:
            excerpt = await code.read(best.path, best.line, context=25)
        except (NotFound, SourceError):
            return items
        items.append(
            _Item(
                "code",
                f"{excerpt.path} lines {excerpt.start}-{excerpt.end} at {rev}",
                excerpt.text,
                self._code_provenance(f"{excerpt.path}:{best.line}@{rev}", excerpt.url),
            )
        )
        return items

    async def _inspect_revision(self, args: dict[str, Any]) -> list[_Item]:
        code = self.sources.code
        if code is None:
            raise SourceError("no code source configured")
        if args.get("node"):
            info = await code.commit(str(args["node"]))
            first_line = (
                (info.get("desc") or "").splitlines()[0] if info.get("desc") else ""
            )
            body = (
                f"Changeset {info['node']}\nAuthor: {info.get('user')}\nDate: {info.get('date')}\n"
                f"Pushed: {info.get('pushdate')}\n\n{info.get('desc', '')}\n\n{info.get('diff', '')}"
            )
            if info.get("diff_truncated"):
                body += "\n[diff truncated]"
            return [
                _Item(
                    "commit",
                    f"changeset {info['node'][:12]}: {first_line}",
                    body,
                    Provenance(
                        source=EvidenceSource.HGMO
                        if code.source == EvidenceSource.SEARCHFOX
                        else code.source,
                        locator=f"changeset {info['node'][:12]}",
                        url=info.get("url"),
                        revision=info["node"],
                    ),
                )
            ]
        path = str(args["file"])
        entries = await code.history(path, limit=10)
        if not entries:
            return []
        listing = "\n".join(
            f"{(e['node'] or '')[:12]} {e['date'][:10]} {one_line(e['desc'], 140)}"
            for e in entries
        )
        return [
            _Item(
                "file_history",
                f"last {len(entries)} changes to {path} up to {code.revision[:12]}",
                listing,
                Provenance(
                    source=EvidenceSource.HGMO
                    if code.source == EvidenceSource.SEARCHFOX
                    else code.source,
                    locator=f"history of {path}@{code.revision[:12]}",
                    url=code.url_for(path),
                    revision=code.revision,
                ),
            )
        ]

    # --- Crash data ----------------------------------------------------------------------

    async def _inspect_crash_evidence(self, args: dict[str, Any]) -> list[_Item]:
        if args.get("crash_id"):
            return await self._crash_report(str(args["crash_id"]))
        return await self._signature(str(args["signature"]))

    async def _crash_report(self, crash_id: str) -> list[_Item]:
        """One crash report; when it is gone or has no usable stack, the latest
        crash of the same signature before the cutoff that has one."""
        stats = self.sources.crash_stats
        try:
            report = await stats.crash_report(crash_id, as_of=self.state.as_of)
        except NotFound:
            if not self.crash_data:
                raise
            report = None
        items, frames = [], []
        if report is not None:
            frames = report.pop("frames", [])
            items.append(
                _crash_item(
                    stats,
                    crash_id,
                    f"crash report {crash_id}: {report.get('signature', '')}",
                    report,
                    frames,
                )
            )
        if self.crash_data and not usable_stack(frames):
            signatures = (
                [report["signature"]] if report and report.get("signature") else []
            )
            snap = self.state.report_snapshot
            signatures += snap.crash_signatures if snap else []
            state = "is gone" if report is None else "has no usable stack"
            other = await self._recover_stack(
                signatures, instead_of=f"crash {crash_id}, which {state}"
            )
            if other is not None:
                items.append(other)
            elif report is None:
                raise NotFound(f"crash {crash_id}, and no other crash of its signature")
        return items

    async def _signature(self, signature: str) -> list[_Item]:
        """A signature's crash counts before the cutoff and, with crash data on,
        the stack of its latest crash."""
        stats = self.sources.crash_stats
        as_of = self.state.as_of
        days = self.sources.crash_stats_window_days
        # A signature that no crash has exactly (it changed with the code, or the
        # report shortened it): the crashes whose signature contains its most
        # specific frames.
        queries = (
            [signature, *contains_queries(signature)]
            if self.crash_data
            else [signature]
        )
        query, data = signature, {"total": 0, "facets": {}}
        for query in queries:
            data = await stats.signature_facets(query, as_of=as_of, window_days=days)
            if data["total"]:
                break
        if not data["total"]:
            return []
        window = f"{days} days before {as_of:%Y-%m-%d}" if as_of else "the last 7 days"
        described = _described(query)
        lines = [f"Crashes {described} in {window}: {data['total']}"]
        for name, terms in data["facets"].items():
            lines.append(
                f"{name}: " + ", ".join(f"{term} ({count})" for term, count in terms)
            )
        notes = crash_stats_notes(data["total"], data["facets"])
        if notes:
            lines += ["Derived from the counts above:", *(f"- {n}" for n in notes)]
        items = [
            _Item(
                "crash_stats",
                f"crash-stats for crashes {described}: {data['total']} ({window})",
                "\n".join(lines),
                Provenance(
                    source=EvidenceSource.CRASH_STATS,
                    locator=f"crash-stats [@ {query}]",
                    url=stats.signature_url(query),
                ),
                facts=[f"Crash-stats ({window}): {note}" for note in notes],
            )
        ]
        if self.crash_data:
            stack = await self._recover_stack([query])
            items += [stack] if stack else []
        return items

    async def _recover_stack(
        self,
        signatures: list[str],
        instead_of: str | None = None,
        max_reports: int = 6,
    ) -> _Item | None:
        """The latest crash with a usable stack before the cutoff, among the
        crashes of these signatures: each exactly, then those whose signature
        contains its most specific frames, going on past crashes without one."""
        stats = self.sources.crash_stats
        as_of = self.state.as_of
        days = self.sources.crash_stats_window_days
        queries = list(
            dict.fromkeys(
                q
                for s in signatures
                if s.strip()
                for q in (s.strip(), *contains_queries(s))
            )
        )
        read = 0
        for query in queries[:4]:
            try:
                crashes = await stats.signature_crashes(
                    query, as_of=as_of, window_days=days, limit=5
                )
            except SourceError:
                continue
            for crash in crashes:
                if read >= max_reports:
                    return None
                read += 1
                try:
                    report = await stats.crash_report(crash["uuid"], as_of=as_of)
                except SourceError:
                    continue
                frames = report.pop("frames", [])
                if usable_stack(frames):
                    return _crash_item(
                        stats,
                        crash["uuid"],
                        f"crash report {crash['uuid']} ({crash['date']})"
                        + (f", instead of {instead_of}" if instead_of else "")
                        + f": the latest crash {_described(query)} before the "
                        "cutoff with a usable stack",
                        report,
                        frames,
                    )
        return None

    async def _search_prior_bugs(self, args: dict[str, Any]) -> list[_Item]:
        return await _prior_bug_items(self.state, self.sources, args)

    # --- Bugzilla ----------------------------------------------------------------------------

    async def _search_bugzilla(self, args: dict[str, Any]) -> list[_Item]:
        bugzilla = self.sources.bugzilla
        as_of = self.state.as_of
        if args.get("bug_id"):
            bug_id = int(args["bug_id"])
            if bug_id == self.state.bug_id:
                return []
            view = await bugzilla.view(bug_id, as_of)
            fields = view.fields
            parts = [
                f"Bug {bug_id}: {fields.get('summary')}",
                f"{fields.get('product')} :: {fields.get('component')}, created {fields.get('creation_time')}",
            ]
            if view.signatures:
                parts.append("Signatures: " + " | ".join(view.signatures))
            for comment in view.comments[:3]:
                limit = 2_500 if comment.count == 0 else 800
                parts.append(
                    f"Comment {comment.count} ({comment.created:%Y-%m-%d}): {clip(comment.text, limit)}"
                )
            return [
                _Item(
                    "related_bug",
                    f"bug {bug_id}: {fields.get('summary')}",
                    "\n\n".join(parts),
                    Provenance(
                        source=EvidenceSource.BUGZILLA,
                        locator=f"bug {bug_id}" + (" (as of cutoff)" if as_of else ""),
                        url=bugzilla.bug_url(bug_id),
                    ),
                )
            ]
        if args.get("signature"):
            fragment = (
                str(args["signature"]).removeprefix("[@").removesuffix("]").strip()
            )
            bugs = await bugzilla.search_signature(
                fragment, exclude=self.state.bug_id, as_of=as_of
            )
            what = f"bugs with signature containing '{fragment}'"
        else:
            query = str(args["query"])
            bugs = await bugzilla.quicksearch(
                query, exclude=self.state.bug_id, as_of=as_of
            )
            # Quicksearch ANDs every word; a model's phrase rarely matches whole.
            # Fall back to its most code-like word.
            narrower = _most_specific_word(query)
            if not bugs and narrower and narrower != query:
                query = narrower
                bugs = await bugzilla.quicksearch(
                    query, exclude=self.state.bug_id, as_of=as_of
                )
            what = f"bugs matching '{query}'"
        if not bugs:
            return []
        listing = "\n".join(
            f"bug {b['id']} ({b.get('creation_time', '')[:10]}) [{b.get('product')} :: {b.get('component')}] "
            f"{one_line(b.get('summary', ''), 150)}"
            + (f" {{{b['status']} {b.get('resolution', '')}}}" if "status" in b else "")
            for b in bugs
        )
        return [
            _Item(
                "bug_search",
                f"{len(bugs)} {what}",
                listing,
                Provenance(
                    source=EvidenceSource.BUGZILLA, locator=f"search: {what}", url=None
                ),
            )
        ]


# --- Earlier bugs ---------------------------------------------------------------------


async def _prior_bug_items(
    state: CrashInformationState, sources: Sources, args: dict[str, Any]
) -> list[_Item]:
    shown = {
        int(m.group(1))
        for e in state.evidence
        if e.kind == "prior_bug" and (m := re.match(r"bug (\d+)", e.provenance.locator))
    }
    cases = await PriorBugs(state, sources).find(
        args.get("query") or None, limit=int(args.get("limit") or 3), skip=shown
    )
    return [
        _Item(
            "prior_bug",
            case.headline(),
            case.text(),
            Provenance(
                source=EvidenceSource.BUGZILLA,
                locator=f"bug {case.bug_id} (fixed before the cutoff)",
                url=sources.bugzilla.bug_url(case.bug_id),
                revision=case.fix["node"] if case.fix else None,
            ),
        )
        for case in cases
    ]


def _described(query: str) -> str:
    return (
        f"whose signature contains '{query[1:]}'"
        if query.startswith("~")
        else f"with signature [@ {query}]"
    )


def _crash_item(
    stats: Any, crash_id: str, summary: str, report: dict, frames: list[str]
) -> _Item:
    body = "\n".join(f"{k}: {v}" for k, v in report.items())
    if frames:
        body += "\nCrashing thread frames:\n" + "\n".join(frames)
    return _Item(
        "crash_report",
        summary,
        body,
        Provenance(
            source=EvidenceSource.CRASH_STATS,
            locator=f"crash {crash_id}",
            url=stats.report_url(crash_id),
        ),
    )


# A frame without a symbol: a bare offset, or nothing known.
_UNSYMBOLED = re.compile(r"(\s0x[0-9a-f]+|\s\?)$", re.IGNORECASE)


def usable_stack(frames: list[str]) -> bool:
    """At least two frames name their function: enough to find the code."""
    return sum(1 for f in frames if not _UNSYMBOLED.search(f.strip())) >= 2


def stack_frames_in(content: str) -> list[str]:
    """The frames an evidence item about a crash report lists."""
    _, found, rest = content.partition("Crashing thread frames:\n")
    return rest.splitlines() if found else []


def _share(count: int, total: int) -> str:
    return f"{count} of {total} ({100 * count / total:.0f}%)"


# Signature frames that name no code of their own: crash helpers, container
# accessors, the standard libraries, system modules and bare addresses.
_GENERIC_FRAME = re.compile(
    r"_CRASH\b|^MOZ_|^mozilla::detail::|^nsTArray_Impl|^(std|core|alloc)::"
    r"|abort|panic|\.(so|dll|dylib)\b|^libc\b|@0x",
    re.IGNORECASE,
)


def contains_queries(signature: str, limit: int = 2) -> list[str]:
    """'Contains' searches for a signature that no crash has exactly: its most
    specific frames (qualified code names, in the signature's order)."""
    frames = [f.strip() for f in signature.strip().strip("[]@ ").split("|")]
    specific = [f for f in frames if "::" in f and not _GENERIC_FRAME.search(f)]
    return [f"~{f}" for f in specific[:limit] if f != signature.strip()]


def crash_stats_notes(
    total: int, facets: dict[str, list[tuple[str, int]]]
) -> list[str]:
    """Plain-language readings of the facet counts that a small model would
    otherwise have to work out (and often does not)."""
    if not total:
        return []
    notes = []
    near_null = sum(
        count
        for address, count in facets.get("address", [])
        if address.startswith("0x") and int(address, 16) < 0x10000
    )
    if near_null:
        notes.append(
            f"{_share(near_null, total)} crashes are at a near-null address, "
            "i.e. a null (or nearly null) pointer was dereferenced"
        )
    for facet, what in (
        ("product", "product"),
        ("process_type", "process type"),
        ("platform", "platform"),
        ("release_channel", "release channel"),
    ):
        terms = facets.get(facet) or []
        if terms and terms[0][1] / total >= 0.8:
            notes.append(
                f"{_share(terms[0][1], total)} crashes are in {what} '{terms[0][0]}'"
            )
    if "startup_crash" in facets:
        startup = sum(
            count
            for term, count in facets["startup_crash"]
            if term.lower() in ("t", "true", "1")
        )
        notes.append(
            f"{_share(startup, total)} are startup crashes"
            if startup
            else "none are startup crashes"
        )
    if facets.get("shutdown_progress"):
        shutdown = sum(count for _, count in facets["shutdown_progress"])
        stage = facets["shutdown_progress"][0][0]
        notes.append(f"{_share(shutdown, total)} happened during shutdown ({stage})")
    versions = facets.get("version") or []
    if versions and versions[0][1] / total >= 0.5:
        notes.append(
            f"{_share(versions[0][1], total)} crashes are on version {versions[0][0]}"
        )
    return notes


def _most_specific_word(query: str) -> str | None:
    words = [w for w in re.findall(r"[\w:]+", query) if len(w) >= 5]
    codeish = [w for w in words if "::" in w or re.search(r"[a-z][A-Z]", w) or "_" in w]
    return (codeish or sorted(words, key=len, reverse=True) or [None])[0]


def _best_hit(hits: list[CodeHit]) -> CodeHit:
    for hit in hits:
        if "Definition" in hit.section:
            return hit
    for hit in hits:
        if hit.kind == "normal" and hit.path.endswith(
            (".cpp", ".cc", ".rs", ".mm", ".mjs", ".js")
        ):
            return hit
    return hits[0]
