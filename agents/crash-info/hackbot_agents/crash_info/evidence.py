"""The evidence inventory: every item the agent saw, with an id and provenance."""

import hashlib
import re

from .models import CrashInformationState, Evidence, Provenance
from .prompting import clip, one_line


class EvidenceStore:
    def __init__(self, state: CrashInformationState) -> None:
        self.state = state
        self._by_id = {e.id: e for e in state.evidence}
        self._by_key = {
            _key(e.provenance.locator, e.content): e for e in state.evidence
        }

    def add(
        self,
        kind: str,
        summary: str,
        content: str,
        provenance: Provenance,
        action_id: str | None = None,
    ) -> tuple[Evidence, bool]:
        """Record an item; returns ``(evidence, is_new)``. Re-reading the same
        content at the same place yields the existing item, so "new evidence"
        stays meaningful for progress tracking."""
        key = _key(provenance.locator, content)
        if key in self._by_key:
            return self._by_key[key], False
        evidence = Evidence(
            id=self.state.next_id("ev"),
            kind=kind,
            summary=one_line(summary, 240),
            content=content,
            provenance=provenance,
            action_id=action_id,
        )
        self.state.evidence.append(evidence)
        self._by_id[evidence.id] = evidence
        self._by_key[key] = evidence
        return evidence, True

    def get(self, evidence_id: str) -> Evidence | None:
        return self._by_id.get(evidence_id)

    def exists(self, evidence_id: str) -> bool:
        return evidence_id in self._by_id

    def render(
        self, ids: list[str], *, budget: int, summaries_only: bool = False
    ) -> str:
        """Evidence for a prompt, sharing ``budget`` characters across items."""
        items = [self._by_id[i] for i in ids if i in self._by_id]
        if not items:
            return "(none)"
        each = max(300, budget // len(items))
        blocks = []
        for e in items:
            head = evidence_head(e)
            blocks.append(
                head if summaries_only else f"{head}\n{clip(e.content, each)}"
            )
        return "\n\n".join(blocks)


def evidence_head(e: Evidence) -> str:
    """An item's header line. Its source and place are left out when the summary
    already names them, as for code ("a.cpp lines 1-60 at <rev>")."""
    where = e.provenance.locator.split("@")[0]
    if where.split(":")[0] in e.summary or where.removeprefix("search ") in e.summary:
        return f"[{e.id}] {e.summary}"
    return f"[{e.id}] ({e.provenance.source.value}: {e.provenance.locator}) {e.summary}"


def _key(locator: str, content: str) -> str:
    return hashlib.sha1(f"{locator}\n{content}".encode()).hexdigest()


def resolve_ids(raw_ids: list[str], known: set[str]) -> list[str]:
    """Map a model's spellings of ids ("[ev-3]", "EV-003", "3", "fact 2") to
    known ids; unknown ones are dropped."""
    out: list[str] = []
    for raw in raw_ids:
        text = str(raw).strip().strip("[](){}<>").strip().lower()
        candidates = [text]
        number = re.search(r"\d+", text)
        if number:
            prefix = "fact" if text.startswith("fact") else "ev"
            candidates.append(f"{prefix}-{int(number.group()):03d}")
        match = next((c for c in candidates if c in known), None)
        if match and match not in out:
            out.append(match)
    return out
