"""A local Firefox git checkout (e.g. the one Hackbot prepares from ``[source]``)."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from .http import NotFound, SourceError
from .searchfox import CodeHit


class LocalRepo:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def _git(self, *args: str, allow_codes: tuple[int, ...] = (0,)) -> str:
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self.path),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        if process.returncode not in allow_codes:
            message = err.decode(errors="replace").strip()
            if "does not exist" in message or "exists on disk, but not in" in message:
                raise NotFound(message)
            raise SourceError(f"git {' '.join(args[:2])} failed: {message[:300]}")
        return out.decode(errors="replace")

    async def resolve(self, ref: str = "HEAD") -> str:
        return (await self._git("rev-parse", ref)).strip()

    async def revision_at(self, when: datetime) -> str | None:
        out = await self._git("rev-list", "-1", f"--before={when.isoformat()}", "HEAD")
        return out.strip() or None

    async def grep(
        self, pattern: str, rev: str, *, path_prefix: str | None = None, limit: int = 20
    ) -> list[CodeHit]:
        args = ["grep", "-n", "-I", "-F", "-i", "-e", pattern, rev]
        if path_prefix:
            args += ["--", path_prefix]
        # git grep exits 1 when nothing matches.
        out = await self._git(*args, allow_codes=(0, 1))
        hits = []
        for raw in out.splitlines():
            # "<rev>:<path>:<line>:<text>"
            parts = raw.split(":", 3)
            if len(parts) == 4 and parts[2].isdigit():
                hits.append(CodeHit(parts[1], int(parts[2]), parts[3].strip()))
            if len(hits) >= limit:
                break
        return hits

    async def show(self, rev: str, path: str) -> str:
        return await self._git("show", f"{rev}:{path}")

    async def log(self, rev: str, path: str, limit: int = 10) -> list[dict[str, Any]]:
        out = await self._git(
            "log", f"-n{limit}", "--format=%H%x1f%cI%x1f%an%x1f%s", rev, "--", path
        )
        entries = []
        for line in out.splitlines():
            node, date, user, desc = (line.split("\x1f") + ["", "", "", ""])[:4]
            entries.append({"node": node, "date": date, "user": user, "desc": desc})
        return entries

    async def commit(self, node: str) -> dict[str, Any]:
        out = await self._git("show", "-s", "--format=%H%x1f%cI%x1f%an%x1f%B", node)
        full, date, user, desc = (out.split("\x1f", 3) + ["", "", "", ""])[:4]
        return {
            "node": full.strip(),
            "user": user,
            "date": date,
            "commit_time": datetime.fromisoformat(date) if date else None,
            "desc": desc.strip(),
        }

    async def diff(self, node: str) -> str:
        return await self._git("show", "--format=%H %s", "--patch", node)
