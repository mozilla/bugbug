"""Searchfox code search (current tip of the indexed tree only)."""

from dataclasses import dataclass
from urllib.parse import urlencode

from .http import HttpClient

_KIND_ORDER = {"normal": 0, "generated": 1, "test": 2, "thirdparty": 3}


@dataclass(frozen=True)
class CodeHit:
    path: str
    line: int
    text: str
    kind: str = "normal"
    section: str = ""


class SearchfoxClient:
    def __init__(self, http: HttpClient, base_url: str, repo: str) -> None:
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.repo = repo

    def search_url(self, query: str, path_prefix: str | None = None) -> str:
        params = {"q": query, **({"path": path_prefix} if path_prefix else {})}
        return f"{self.base_url}/{self.repo}/search?{urlencode(params)}"

    def source_url(self, path: str, line: int | None = None) -> str:
        url = f"{self.base_url}/{self.repo}/source/{path}"
        return f"{url}#{line}" if line else url

    async def search(
        self,
        query: str,
        *,
        path_prefix: str | None = None,
        regexp: bool = False,
        case: bool = False,
        limit: int = 20,
    ) -> list[CodeHit]:
        params = {
            "q": query,
            "case": str(case).lower(),
            "regexp": str(regexp).lower(),
        }
        if path_prefix:
            params["path"] = path_prefix
        data = await self.http.get_json(
            f"{self.base_url}/{self.repo}/search",
            params,
            headers={"Accept": "application/json"},
        )
        hits: dict[tuple[str, int], CodeHit] = {}
        for kind, sections in data.items():
            # Keys like "*title*" and "*timedout*" are metadata, not results.
            if kind.startswith("*") or not isinstance(sections, dict):
                continue
            for section, files in sections.items():
                for entry in files or []:
                    for line in entry.get("lines", []):
                        key = (entry["path"], int(line["lno"]))
                        hits.setdefault(
                            key,
                            CodeHit(
                                entry["path"],
                                key[1],
                                line["line"].strip(),
                                kind,
                                section,
                            ),
                        )
        ordered = sorted(
            hits.values(),
            key=lambda h: (
                "Definition" not in h.section,
                _KIND_ORDER.get(h.kind, 9),
                h.path,
                h.line,
            ),
        )
        return ordered[:limit]
