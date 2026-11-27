"""Turn the crash report into an evidence inventory, without a model.

Each part of the report as of the cutoff (fields, comments, attachments) becomes
an evidence item, and parsing collects what regexes find reliably: stack
frames, crash ids, crash reasons, files, identifiers.
"""

import re

from .evidence import EvidenceStore
from .models import (
    CrashInformationState,
    EvidenceSource,
    Provenance,
    ReportAttachment,
    ReportComment,
    ReportIndicators,
    ReportSnapshot,
    StackFrame,
)
from .prompting import clip
from .sources.bugzilla import BugView

SNAPSHOT_FIELDS = (
    "product",
    "component",
    "status",
    "resolution",
    "type",
    "severity",
    "priority",
    "platform",
    "op_sys",
    "version",
    "keywords",
    "whiteboard",
    "regressed_by",
    "depends_on",
    "blocks",
    "see_also",
    "cf_crash_signature",
)
MAX_COMMENT_CHARS = 6_000

# --- Deterministic indicators ------------------------------------------------

_CRASH_ID = re.compile(
    r"(?:crash-stats\.mozilla\.org/report/index/|\bbp-)"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
_REASON = re.compile(
    r"\b(EXCEPTION_[A-Z_]+|EXC_[A-Z_]+(?: / [A-Z_]+)?|SIG(?:SEGV|BUS|ABRT|ILL|FPE|TRAP)"
    r"(?: / [A-Z_]+)?|MOZ_(?:RELEASE_|DIAGNOSTIC_)?ASSERT\([^)\n]{0,160}\)"
    r"|MOZ_CRASH\([^)\n]{0,160}\))"
)
_FILE = re.compile(
    r"\b((?:[\w.+-]+/)+[\w.+-]+\.(?:cpp|cc|cxx|c|h|hh|hpp|rs|mm|js|mjs|jsm|py|idl|webidl"
    r"|html|xhtml|toml|ini|yaml))\b"
)
_QUALIFIED = re.compile(r"\b(?:[A-Za-z_]\w*::)+~?[A-Za-z_]\w*\b")
_BACKTICKED = re.compile(r"`([A-Za-z_][\w:.>-]{2,80})`")
_BUG_REF = re.compile(r"\b[Bb]ug\s*#?\s*(\d{5,8})\b")
_MODULE = re.compile(r"^(?:[\w.+-]+\.(?:dll|so(?:\.\d+)*|dylib|exe)|XUL|xul)$")
_BUILD_PREFIX = re.compile(
    r"^.*?/(?:checkouts/gecko|gecko-dev|mozilla-central|firefox)/"
)
# Smart-pointer, std and XPConnect/JS-call plumbing: rarely where a crash is fixed.
_BORING_FRAME = re.compile(
    r"^(?:RefPtr|nsCOMPtr|mozilla::detail|std::|already_AddRefed|XPTC_|NS_InvokeByIndex"
    r"|CallMethodHelper::|XPCWrappedNative::|XPC_WN_|CallJSNative|js::(?:Call|Invoke|"
    r"Interpret|RunScript|InternalCall)|Interpret$|RtlUserThreadStart|BaseThreadInitThunk)"
)


def clean_path(path: str | None) -> str | None:
    """Repo-relative path from Socorro and build-machine spellings."""
    if not path:
        return None
    if path.startswith(("hg:", "git:")):
        # "hg:hg.mozilla.org/mozilla-central:dom/base/nsINode.h:<rev>"
        parts = path.split(":")
        path = parts[2] if len(parts) > 2 else path
    path = path.replace("\\", "/")
    return _BUILD_PREFIX.sub("", path).lstrip("/")


def clean_function(function: str) -> str:
    function = function.strip()
    if "(" in function and not function.startswith("operator"):
        function = function[: function.index("(")]
    return function.removesuffix(" const").strip()


def parse_frame(line: str) -> StackFrame | None:
    line = line.strip().strip("`")
    if line.count("|") >= 2:
        parts = [p.strip() for p in line.split("|")]
    else:
        parts = [p.strip() for p in re.split(r"\t+|\s{2,}", line)]
    parts = [p for p in parts if p]
    if len(parts) < 2 or not parts[0].lstrip("#").isdigit():
        return None
    location = re.match(r"(.+?):(\d+)$", parts[-1])
    rest = parts[1:-1] if location else parts[1:]
    if not rest:
        return None
    module = rest[0] if len(rest) > 1 and _MODULE.match(rest[0]) else None
    function = clean_function(" ".join(rest[1:] if module else rest))
    if not function or function.isdigit():
        return None
    return StackFrame(
        index=int(parts[0].lstrip("#")),
        module=module,
        function=function,
        file=clean_path(location.group(1)) if location else None,
        line=int(location.group(2)) if location else None,
    )


def extract_indicators(texts: list[str], bug_id: int) -> ReportIndicators:
    frames: list[StackFrame] = []
    seen_frames: set[tuple[str, str | None]] = set()
    crash_ids: list[str] = []
    reasons: list[str] = []
    files: list[str] = []
    identifiers: list[str] = []
    bug_refs: list[int] = []

    def add(items: list, value) -> None:
        if value and value not in items:
            items.append(value)

    for text in texts:
        for line in text.splitlines():
            frame = parse_frame(line)
            if frame and (frame.function, frame.file) not in seen_frames:
                seen_frames.add((frame.function, frame.file))
                frames.append(frame)
        for match in _CRASH_ID.findall(text):
            add(crash_ids, match)
        for match in _REASON.findall(text):
            add(reasons, match)
        for match in _FILE.findall(text):
            path = clean_path(match)
            if path and "obj-" not in path and not path.startswith("builds/"):
                add(files, path)
        for match in _QUALIFIED.findall(text) + _BACKTICKED.findall(text):
            add(identifiers, match)
        for match in _BUG_REF.findall(text):
            if int(match) != bug_id:
                add(bug_refs, int(match))
    for frame in frames:
        add(files, frame.file)
    return ReportIndicators(
        stack_frames=frames[:30],
        crash_ids=crash_ids[:10],
        crash_reasons=reasons[:8],
        files=files[:25],
        identifiers=identifiers[:30],
        bug_refs=bug_refs[:15],
    )


def interesting_frames(
    indicators: ReportIndicators, limit: int = 4
) -> list[StackFrame]:
    """Top frames worth searching for: skip smart-pointer and std plumbing."""
    frames = [f for f in indicators.stack_frames if not _BORING_FRAME.match(f.function)]
    return frames[:limit]


# --- Snapshot -----------------------------------------------------------------


def build_snapshot(
    state: CrashInformationState, view: BugView, store: EvidenceStore, bug_url: str
) -> None:
    """Record the report as the agent first saw it, one evidence item per part."""
    fields = view.fields

    def original(locator: str, url: str) -> Provenance:
        return Provenance(
            source=EvidenceSource.BUGZILLA, locator=locator, url=url, original=True
        )

    bug_id = state.bug_id
    summary = fields.get("summary") or ""
    store.add(
        "report_summary",
        f"Summary: {summary}",
        summary,
        original(f"bug {bug_id} summary", bug_url),
    )
    field_text = "; ".join(
        f"{name}={_fmt(fields.get(name))}"
        for name in SNAPSHOT_FIELDS
        if fields.get(name)
    )
    store.add(
        "report_fields",
        "Bug fields",
        field_text,
        original(f"bug {bug_id} fields", bug_url),
    )

    comments = []
    for c in view.comments:
        evidence, _ = store.add(
            "comment",
            f"Comment {c.count} by {c.author.split('@')[0]} ({c.created:%Y-%m-%d})",
            clip(c.text, MAX_COMMENT_CHARS),
            original(f"bug {bug_id} comment {c.count}", f"{bug_url}#c{c.count}"),
        )
        comments.append(
            ReportComment(
                id=c.id,
                count=c.count,
                author=c.author,
                created=c.created,
                text=c.text,
                evidence_id=evidence.id,
            )
        )
    attachments = []
    for a in view.attachments:
        evidence, _ = store.add(
            "attachment",
            f"Attachment {a.id}: {a.description}",
            f"{a.description} ({a.content_type}, {a.file_name}, {a.created:%Y-%m-%d})",
            original(
                f"bug {bug_id} attachment {a.id}",
                f"{bug_url.split('show_bug')[0]}attachment.cgi?id={a.id}",
            ),
        )
        attachments.append(
            ReportAttachment(
                id=a.id,
                created=a.created,
                description=a.description,
                content_type=a.content_type,
                file_name=a.file_name,
                is_patch=a.looks_like_patch,
                evidence_id=evidence.id,
            )
        )
    state.report_snapshot = ReportSnapshot(
        bug_id=bug_id,
        url=bug_url,
        summary=summary,
        product=fields.get("product"),
        component=fields.get("component"),
        status=fields.get("status"),
        resolution=fields.get("resolution") or None,
        fields={k: fields.get(k) for k in SNAPSHOT_FIELDS if fields.get(k)},
        crash_signatures=view.signatures,
        comments=comments,
        attachments=attachments,
        hidden_after_cutoff=view.hidden,
        rolled_back_fields=view.rolled_back,
    )
    state.indicators = extract_indicators(
        [summary, *(c.text for c in view.comments)], bug_id
    )


def _fmt(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value).replace("\r", " ").replace("\n", " ")


def report_text(state: CrashInformationState, store: EvidenceStore, budget: int) -> str:
    """The report for a prompt: description first and largest, then the rest."""
    snap = state.report_snapshot
    assert snap is not None
    head_ids = [
        e.id
        for e in state.evidence
        if e.provenance.original and e.kind.startswith("report_")
    ]
    blocks = [
        f"[{store.get(i).id}] {store.get(i).summary}: {store.get(i).content}"
        for i in head_ids
    ]
    remaining = budget - sum(len(b) for b in blocks)
    comments = snap.comments
    if comments:
        first_share = (
            max(1_500, int(remaining * 0.4)) if len(comments) > 1 else remaining
        )
        blocks.append(_comment_block(store, comments[0], first_share))
        remaining -= len(blocks[-1])
        rest = comments[1:]
        if rest:
            each = max(300, remaining // len(rest))
            for shown, comment in enumerate(rest):
                block = _comment_block(store, comment, each)
                if remaining - len(block) < 0 and shown:
                    blocks.append(
                        f"[{len(rest) - shown} later comments omitted for length]"
                    )
                    break
                blocks.append(block)
                remaining -= len(block)
    for attachment in snap.attachments:
        evidence = store.get(attachment.evidence_id)
        blocks.append(f"[{evidence.id}] {evidence.content}")
    return "\n\n".join(blocks)


def _comment_block(store: EvidenceStore, comment: ReportComment, limit: int) -> str:
    evidence = store.get(comment.evidence_id)
    return f"[{evidence.id}] {evidence.summary}:\n{clip(comment.text, limit)}"


def report_digest(state: CrashInformationState) -> str:
    """Compact summary of the report, ahead of the full text in the task prompt."""
    snap = state.report_snapshot
    assert snap is not None
    ind = state.indicators
    lines = [f"Bug {snap.bug_id}: {snap.summary}"]
    if snap.product:
        lines.append(f"Product/component: {snap.product} :: {snap.component}")
    if snap.crash_signatures:
        lines.append("Crash signature(s): " + " | ".join(snap.crash_signatures))
    if ind.crash_reasons:
        lines.append("Crash reason(s): " + ", ".join(ind.crash_reasons))
    if ind.stack_frames:
        lines.append(
            "Top frames: "
            + "; ".join(
                f"{f.function}" + (f" ({f.file}:{f.line})" if f.file else "")
                for f in ind.stack_frames[:8]
            )
        )
    env = [
        f"{k}={snap.fields[k]}"
        for k in ("op_sys", "platform", "version")
        if k in snap.fields
    ]
    if env:
        lines.append("Report fields: " + ", ".join(env))
    return "\n".join(lines)
