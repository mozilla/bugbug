# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Keep one example's state out of the next: fresh home and temp, own process group.

The harnesses keep memory and sessions under HOME (``~/.claude``, ``~/.codex``),
so each example starts from an empty one. The build toolchain installed by
``mach bootstrap`` is shared, since it does not carry anything between examples.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path


def example_env(
    base: dict[str, str], root: Path, extra: dict[str, str]
) -> dict[str, str]:
    home, tmp = root / "home", root / "tmp"
    # Codex won't start unless its config directory exists.
    for path in (home / ".claude", home / ".codex", tmp):
        path.mkdir(parents=True, exist_ok=True)
    real_home = Path(base.get("HOME", Path.home()))
    return {
        **base,
        **extra,
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CODEX_HOME": str(home / ".codex"),
        "MOZBUILD_STATE_PATH": base.get(
            "MOZBUILD_STATE_PATH", str(real_home / ".mozbuild")
        ),
        "CARGO_HOME": base.get("CARGO_HOME", str(real_home / ".cargo")),
        "RUSTUP_HOME": base.get("RUSTUP_HOME", str(real_home / ".rustup")),
    }


def _kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGKILL)


async def _tee(stream: asyncio.StreamReader, log, prefix: str) -> None:
    async for line in stream:
        log.write(line)
        sys.stdout.write(prefix + line.decode(errors="replace"))
        sys.stdout.flush()


async def run_isolated(
    args: list[str],
    env: dict[str, str],
    cwd: Path,
    timeout_s: float,
    log: Path,
    prefix: str = "",
) -> int | None:
    """Run ``args`` in its own process group; None when it hit ``timeout_s``.

    Its output goes to ``log`` and, prefixed, to this process's stdout. The whole
    group is killed afterwards either way, so no build, browser or test process
    outlives the example.
    """
    with log.open("wb") as out:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        reader = asyncio.create_task(_tee(proc.stdout, out, prefix))
        try:
            return await asyncio.wait_for(proc.wait(), timeout_s)
        except TimeoutError:
            return None
        finally:
            _kill_group(proc.pid)
            await proc.wait()
            await reader
