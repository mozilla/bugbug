"""Code context interfaces shared by review agents and other runtimes."""

from collections.abc import Sequence
from typing import Protocol


class CodeContext(Protocol):
    """A revision-aware source provider; descriptions state search limitations."""

    description: str
    capabilities: frozenset[str]

    async def read_file(self, path: str) -> str: ...

    async def search_text(
        self,
        query: str,
        *,
        path_filter: str | None = None,
        regexp: bool = False,
        case_sensitive: bool = False,
        limit: int = 50,
        context_lines: int | None = None,
        langs: Sequence[str] | None = None,
        tests: str | None = None,
    ) -> str: ...

    async def search_identifier(
        self,
        identifier: str,
        *,
        path_filter: str | None = None,
        langs: Sequence[str] | None = None,
        tests: str | None = None,
        limit: int = 50,
    ) -> str: ...
