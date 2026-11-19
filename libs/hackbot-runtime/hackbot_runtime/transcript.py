"""Shared logging helpers for Claude and Codex reporters.

Each reporter handles its SDK's events and uses these helpers to write short
messages to stdout and full records to the run log, with UTC timestamps.
"""

import time
from pathlib import Path


def _truncate(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"


def _stamp() -> str:
    """UTC wall clock, HH:MM:SS.mmm, prefixed to every log-file record."""
    now = time.time()
    clock = time.strftime("%H:%M:%S", time.gmtime(now))
    millis = int(now * 1000) % 1000
    return f"{clock}.{millis:03d}"


class TranscriptLog:
    """Writes transcript lines to stdout and/or a log file."""

    def __init__(self, verbose: bool, log_path: Path | None):
        self.verbose = verbose
        self._log = log_path.open("a", encoding="utf-8") if log_path else None
        self._turn = 0
        self._started = time.monotonic()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._log:
            self._log.close()

    def header(self, title: str) -> None:
        """Emit a section header (e.g. ``"bug 12345"``) and reset the turn count."""
        self._turn = 0
        self._started = time.monotonic()
        banner = f"\n{'#' * 60}\n# {title}\n# started {_stamp()} UTC\n{'#' * 60}"
        self._emit(banner, always=True, stamp=False)

    def _emit(
        self,
        line: str,
        *,
        always: bool = False,
        full: str | None = None,
        stamp: bool = True,
    ):
        if self._log:
            text = full if full is not None else line
            lead, text = ("\n", text[1:]) if text.startswith("\n") else ("", text)
            prefix = f"{_stamp()} " if stamp else ""
            self._log.write(f"{lead}{prefix}{text}\n")
            self._log.flush()
        if always or self.verbose:
            print(line)
