"""Code access at one revision, whatever the backend.

``RemoteCode`` combines Searchfox (to find candidate files fast) with
hg.mozilla.org (to read files and history at a revision). Searchfox only knows
the current tip, so when the revision is pinned every hit is re-located in the
file *as it was at that revision*; lines that only exist after the cutoff never
reach the model. ``LocalCode`` does everything with git on a local checkout.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..models import EvidenceSource
from .hgmo import HgmoClient
from .http import LeakageBlocked, NotFound
from .local_repo import LocalRepo
from .searchfox import CodeHit, SearchfoxClient

MAX_DIFF_CHARS = 12_000


@dataclass
class CodeExcerpt:
    path: str
    start: int
    end: int
    text: str
    revision: str
    url: str


def numbered(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + i:>5}: {line}" for i, line in enumerate(lines))


def excerpt_from(
    text: str, path: str, line: int, context: int, revision: str, url: str
) -> CodeExcerpt:
    lines = text.splitlines()
    line = min(max(1, line), max(1, len(lines)))
    start = max(1, line - context)
    end = min(len(lines), line + context)
    return CodeExcerpt(
        path, start, end, numbered(lines[start - 1 : end], start), revision, url
    )


def focus_line(text: str, line: int, function: str, context: int) -> int:
    """``line`` if ``function`` appears near it, else the function's definition."""
    lines = text.splitlines()
    name = function.split("::")[-1]
    if not name:
        return line
    start, end = max(0, line - context - 1), min(len(lines), line + context)
    if line > 0 and any(name in candidate for candidate in lines[start:end]):
        return line
    qualified = re.compile(r"(?<![\w])" + re.escape(function) + r"\s*\(")
    called = re.compile(r"(?<![\w])" + re.escape(name) + r"\s*\(")
    for pattern in (qualified, called):
        for number, candidate in enumerate(lines, 1):
            if pattern.search(candidate) and not candidate.rstrip().endswith(";"):
                return number
    return line


_STRINGS_AND_COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//.*')


def definition_line(
    lines: list[str], function: str, near: int, reach: int = 400
) -> int | None:
    """The line defining ``function`` at or before ``near`` (else after it)."""
    name = function.split("::")[-1]
    pattern = re.compile(r"(?<![\w])" + re.escape(name) + r"\s*\(")

    def is_definition(index: int) -> bool:
        text = lines[index]
        return bool(pattern.search(text)) and not text.rstrip().endswith(";")

    near = min(max(1, near), len(lines))
    for index in range(near - 1, max(-1, near - 1 - reach), -1):
        if is_definition(index):
            return index + 1
    for index in range(near, min(len(lines), near + reach)):
        if is_definition(index):
            return index + 1
    return None


def function_span(
    lines: list[str], start: int | None, cap: int = 220
) -> tuple[int, int] | None:
    """(first, last) lines of the brace-delimited body starting at ``start``."""
    if start is None:
        return None
    depth, opened = 0, False
    for index in range(start - 1, min(len(lines), start - 1 + cap)):
        code = _STRINGS_AND_COMMENTS.sub("", lines[index])
        depth += code.count("{") - code.count("}")
        opened = opened or "{" in code
        if opened and depth <= 0:
            return max(1, start - 3), index + 1
    return None


def _matcher(query: str):
    """Case-insensitive match; identifiers match on word boundaries."""
    if re.fullmatch(r"[\w:~]+", query):
        pattern = re.compile(r"(?<![\w])" + re.escape(query) + r"(?![\w])", re.I)
    else:
        pattern = re.compile(re.escape(query), re.I)
    return pattern.search


class CodeSource(ABC):
    source: EvidenceSource
    revision: str

    def __init__(self, as_of: datetime | None) -> None:
        self.as_of = as_of

    @property
    def pinned(self) -> bool:
        return self.as_of is not None

    @abstractmethod
    async def search(
        self, query: str, path_prefix: str | None = None, limit: int = 12
    ) -> list[CodeHit]: ...

    @abstractmethod
    async def text(self, path: str) -> str:
        """The whole file at this source's revision."""

    async def read(
        self, path: str, line: int, context: int = 30, *, focus: str | None = None
    ) -> CodeExcerpt:
        """Lines around ``line``. With ``focus`` (a function name), the whole
        function when it can be delimited, else the window re-centred on it: a
        crash report's line numbers come from the crashing build, not this
        revision, and a mechanism is rarely visible in a 60-line window."""
        text = await self.text(path)
        if focus:
            line = focus_line(text, line, focus, context)
            lines = text.splitlines()
            span = function_span(lines, definition_line(lines, focus, line))
            if span:
                start, end = span
                return CodeExcerpt(
                    path,
                    start,
                    end,
                    numbered(lines[start - 1 : end], start),
                    self.revision,
                    self.url_for(path, start),
                )
        return excerpt_from(
            text, path, line, context, self.revision, self.url_for(path, line)
        )

    @abstractmethod
    async def history(self, path: str, limit: int = 10) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def commit(self, node: str) -> dict[str, Any]: ...

    @abstractmethod
    def url_for(self, path: str, line: int | None = None) -> str: ...


class RemoteCode(CodeSource):
    source = EvidenceSource.SEARCHFOX

    def __init__(
        self,
        searchfox: SearchfoxClient,
        hgmo: HgmoClient,
        *,
        revision: str | None,
        as_of: datetime | None,
        max_pinned_files: int = 8,
    ) -> None:
        super().__init__(as_of)
        self.searchfox = searchfox
        self.hgmo = hgmo
        self.revision = revision or "tip"
        self.max_pinned_files = max_pinned_files

    def url_for(self, path: str, line: int | None = None) -> str:
        if self.pinned:
            return self.hgmo.file_url(self.revision, path, line)
        return self.searchfox.source_url(path, line)

    async def search(
        self, query: str, path_prefix: str | None = None, limit: int = 12
    ) -> list[CodeHit]:
        hits = await self.searchfox.search(query, path_prefix=path_prefix, limit=60)
        if not self.pinned:
            return hits[:limit]
        found = _matcher(query)
        relocated: list[CodeHit] = []
        paths = list(dict.fromkeys(h.path for h in hits))[: self.max_pinned_files]
        kinds = {h.path: (h.kind, h.section) for h in hits}
        for path in paths:
            try:
                text = await self.hgmo.file_at(self.revision, path)
            except NotFound:
                continue  # The file did not exist yet at the pinned revision.
            for number, line in enumerate(text.splitlines(), 1):
                if found(line):
                    kind, section = kinds[path]
                    relocated.append(CodeHit(path, number, line.strip(), kind, section))
        return relocated[:limit]

    async def text(self, path: str) -> str:
        return await self.hgmo.file_at(self.revision, path)

    async def history(self, path: str, limit: int = 10) -> list[dict[str, Any]]:
        return await self.hgmo.file_log(self.revision, path, limit)

    async def commit(self, node: str) -> dict[str, Any]:
        info = await self.hgmo.commit(node)
        if self.as_of is not None and (info["push_ts"] or 0) > self.as_of.timestamp():
            raise LeakageBlocked(f"changeset {node} was pushed after the cutoff")
        diff = await self.hgmo.commit_diff(node)
        info["diff"] = diff[:MAX_DIFF_CHARS]
        info["diff_truncated"] = len(diff) > MAX_DIFF_CHARS
        info["url"] = self.hgmo.rev_url(info["node"])
        return info


class LocalCode(CodeSource):
    source = EvidenceSource.LOCAL_REPO

    def __init__(
        self, repo: LocalRepo, *, revision: str, as_of: datetime | None
    ) -> None:
        super().__init__(as_of)
        self.repo = repo
        self.revision = revision

    def url_for(self, path: str, line: int | None = None) -> str:
        return f"{self.repo.path}/{path}" + (f":{line}" if line else "")

    async def search(
        self, query: str, path_prefix: str | None = None, limit: int = 12
    ) -> list[CodeHit]:
        return await self.repo.grep(
            query, self.revision, path_prefix=path_prefix, limit=limit
        )

    async def text(self, path: str) -> str:
        return await self.repo.show(self.revision, path)

    async def history(self, path: str, limit: int = 10) -> list[dict[str, Any]]:
        return await self.repo.log(self.revision, path, limit)

    async def commit(self, node: str) -> dict[str, Any]:
        info = await self.repo.commit(node)
        when = info.pop("commit_time")
        if self.as_of is not None and when is not None and when > self.as_of:
            raise LeakageBlocked(f"commit {node} was made after the cutoff")
        diff = await self.repo.diff(node)
        info["diff"] = diff[:MAX_DIFF_CHARS]
        info["diff_truncated"] = len(diff) > MAX_DIFF_CHARS
        info["url"] = None
        return info
