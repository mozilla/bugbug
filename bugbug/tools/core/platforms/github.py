"""GitHub PR snapshots and remote code context, independent of agent runtimes."""

import asyncio
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from urllib.parse import quote

import httpx

from bugbug.tools.core.platforms.base import Patch


class GitHubAPI(Protocol):
    async def request(self, method: str, path: str, **kwargs) -> httpx.Response: ...


class GitHubSnapshotChanged(ValueError):
    """The PR no longer matches the requested review snapshot."""


def validate_path(path: str) -> str:
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or any(part in (".", "..", "") for part in path.split("/"))
    ):
        raise ValueError("Expected a repository-relative file path")
    return quote(path, safe="/")


class GitHubPatch(Patch):
    """A whole PR diff and ordered commits, pinned to immutable revisions."""

    def __init__(
        self,
        api: GitHubAPI,
        repo: str,
        pr: dict,
        raw_diff: str,
        base_sha: str,
        commits: list[dict],
        commit_diffs: list[str],
    ):
        self.api = api
        self.repo = repo
        self.pr = pr
        self._raw_diff = raw_diff
        self.base_sha = base_sha
        self.head_sha = pr["head"]["sha"]
        self.head_repo = pr["head"]["repo"]["full_name"]
        self.commits = commits
        self.commit_diffs = commit_diffs
        self._files: dict[tuple[str, str, str], str] = {}

    @classmethod
    async def load(
        cls,
        api: GitHubAPI,
        repo: str,
        number: int,
        expected_sha: str | None = None,
    ):
        prefix = f"repos/{repo}"
        pr = (await api.request("GET", f"{prefix}/pulls/{number}")).json()
        head = pr["head"]["sha"]
        if expected_sha and head != expected_sha:
            raise GitHubSnapshotChanged(
                "PR head changed since the review was requested"
            )
        # Compare returns the merge base, which is the source side of the PR diff.
        compare = f"{prefix}/compare/{pr['base']['sha']}...{head}"
        comparison = (await api.request("GET", compare)).json()
        base_sha = comparison["merge_base_commit"]["sha"]
        diff = (
            await api.request(
                "GET", compare, headers={"Accept": "application/vnd.github.diff"}
            )
        ).text
        if len(diff.encode()) > 250_000:
            raise ValueError("PR diff exceeds the review size limit")
        commits = []
        for page in range(1, 4):
            batch = (
                await api.request(
                    "GET",
                    f"{prefix}/pulls/{number}/commits",
                    params={"per_page": 100, "page": page},
                )
            ).json()
            commits.extend(batch)
            if len(batch) < 100:
                break
        if len(commits) != pr["commits"]:
            raise ValueError("Could not retrieve the complete PR commit list")
        commit_diffs = []
        total_bytes = len(diff.encode())
        for commit in commits:
            response = await api.request(
                "GET",
                f"{prefix}/commits/{commit['sha']}",
                headers={"Accept": "application/vnd.github.diff"},
            )
            total_bytes += len(response.content)
            if total_bytes > 500_000:
                raise ValueError("Commit context exceeds the review size limit")
            commit_diffs.append(response.text)
        # Detect pushes while fetching the snapshot, including a changed commit list.
        latest = (await api.request("GET", f"{prefix}/pulls/{number}")).json()
        if latest["head"]["sha"] != head:
            raise GitHubSnapshotChanged("PR changed while fetching the review snapshot")
        return cls(api, repo, pr, diff, base_sha, commits, commit_diffs)

    @property
    def patch_id(self) -> str:
        return f"{self.repo}#{self.pr['number']}@{self.head_sha}"

    @property
    def raw_diff(self) -> str:
        return self._raw_diff

    @property
    def date_created(self) -> datetime:
        return datetime.fromisoformat(self.pr["created_at"].replace("Z", "+00:00"))

    @property
    def has_bug(self) -> bool:
        return False

    @property
    def bug_title(self) -> str:
        return ""

    @property
    def patch_title(self) -> str:
        return self.pr["title"]

    @property
    def patch_description(self) -> str:
        return self.pr.get("body") or ""

    @property
    def patch_url(self) -> str:
        return self.pr["html_url"]

    @property
    def commit_context(self) -> str:
        units = [
            "Ordered PR commits. Treat each as a distinct unit, but verify all "
            "findings in the FINAL PR state. Do not report issues fixed by later "
            "commits. Anchor every comment to a line in the final PR diff above; "
            "commit-local line numbers are not publication coordinates."
        ]
        for commit, diff in zip(self.commits, self.commit_diffs):
            units.append(
                f"Commit {commit['sha']}\n{commit['commit']['message']}\n{diff}"
            )
        return "\n\n".join(units)

    async def github_repo_ref(self) -> tuple[str, str]:
        # Trusted instructions come from the target branch snapshot, not the fork.
        return self.repo, self.pr["base"]["sha"]

    async def get_base_revision(self) -> str:
        return self.base_sha

    async def read_file_at(self, repo: str, sha: str, path: str) -> str:
        key = (repo, sha, path)
        if key not in self._files:
            try:
                response = await self.api.request(
                    "GET",
                    f"repos/{repo}/contents/{validate_path(path)}",
                    params={"ref": sha},
                    headers={"Accept": "application/vnd.github.raw+json"},
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise FileNotFoundError(path) from e
                raise
            if len(response.content) > 1_000_000:
                raise ValueError("File exceeds the context size limit")
            self._files[key] = response.text
        return self._files[key]

    async def get_old_file(self, file_path: str) -> str:
        return await self.read_file_at(self.repo, self.base_sha, file_path)

    async def get_new_file(self, file_path: str) -> str:
        return await self.read_file_at(self.head_repo, self.head_sha, file_path)

    async def get_review_context_file(self, repo: str, ref: str, path: str) -> str:
        """Fetch trusted context through authenticated API access."""
        return await self.read_file_at(repo, ref, path)

    def is_accessible(self) -> bool:
        return True

    def is_public(self) -> bool:
        return not self.pr["base"]["repo"]["private"]


class GitHubCodeContext:
    """Search changed files at PR head, and discover other files with code search."""

    capabilities = frozenset({"read_file", "text_search", "identifier_search"})
    description = (
        "File reads and returned lines reflect the exact PR head. Search scans "
        "changed files and uses GitHub's default-branch index to discover other files. "
        "New or renamed unmodified files may be missing from that index. Identifier "
        "search is textual, not semantic; definition/call-graph/layout tools are unavailable. "
        "Literal/case matching is applied to fetched files. Regex discovery is unavailable."
    )

    def __init__(self, patch: GitHubPatch):
        self.patch = patch
        self._search_cache: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    async def read_file(self, path: str) -> str:
        return await self.patch.get_new_file(path)

    async def search_identifier(
        self,
        identifier: str,
        *,
        path_filter: str | None = None,
        langs: Sequence[str] | None = None,
        tests: str | None = None,
        limit: int = 50,
    ) -> str:
        return await self.search_text(
            identifier,
            path_filter=path_filter,
            langs=langs,
            tests=tests,
            limit=limit,
            identifier=True,
        )

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
        identifier: bool = False,
    ) -> str:
        if not query or len(query) > 200:
            raise ValueError("Search query must contain 1 to 200 characters")
        if regexp:
            return (
                "Regex discovery is unavailable. Search with a literal identifier "
                "or text fragment, then expand_context to inspect matching files."
            )
        pattern = re.escape(query)
        if identifier:
            pattern = rf"(?<!\w){pattern}(?!\w)"
        matcher = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        candidates = {f.path for f in self.patch.patch_set if not f.is_removed_file}
        warnings = []
        # Quoted input remains literal; do not permit repo qualifiers supplied by the model.
        literal = query.replace('"', " ").replace("\\", " ")
        search_query = f'"{literal}" repo:{self.patch.repo}'
        if path_filter:
            validate_path(path_filter.rstrip("/"))
            search_query += f' path:"{path_filter.replace(chr(34), " ")}"'
        async with self._lock:
            if search_query not in self._search_cache:
                try:
                    response = await self.patch.api.request(
                        "GET", "search/code", params={"q": search_query, "per_page": 30}
                    )
                    result = response.json()
                    if result.get("incomplete_results"):
                        warnings.append("GitHub returned incomplete discovery results.")
                    self._search_cache[search_query] = [
                        item["path"] for item in result["items"]
                    ]
                except httpx.HTTPError:
                    warnings.append(
                        "GitHub discovery failed; only changed files were searched."
                    )
            candidates.update(self._search_cache.get(search_query, []))
        if langs or tests:
            warnings.append(
                "Language/test filters are unavailable; use path_filter to narrow results."
            )
        hits = []
        limit = max(1, min(limit, 100))
        surrounding = max(0, min(context_lines or 0, 10))
        for path in sorted(candidates):
            if path_filter and not path.startswith(path_filter):
                continue
            try:
                lines = (await self.read_file(path)).splitlines()
            except FileNotFoundError:
                continue
            except (httpx.HTTPError, ValueError):
                warnings.append(f"Could not inspect {path} at PR head.")
                continue
            shown = set()
            for i, line in enumerate(lines):
                if not matcher.search(line):
                    continue
                for j in range(
                    max(0, i - surrounding), min(len(lines), i + surrounding + 1)
                ):
                    if j not in shown:
                        hits.append(f"{path}:{j + 1}: {lines[j]}")
                        shown.add(j)
                if len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break
        return "\n".join(warnings + (hits[:limit] or ["No matches found at PR head."]))
