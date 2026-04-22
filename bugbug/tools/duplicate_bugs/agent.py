r"""Duplicate bug detector -- find duplicate bugs, three ways.

mode="local"           One crash per sub-directory. For each, decide
                       whether it is already filed as a blocker of
                       meta_bug on Bugzilla.

mode="bugs"            Already-filed bugs. For each, decide whether some
                       *other* blocker of meta_bug covers the same crash.

mode="local_to_local"  One crash per sub-directory, but the directory
                       still contains internal duplicates. Groups the
                       sub-directories by crash and copies one
                       representative per group into results_dir.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import bugsy
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from bugbug.tools.base import GenerativeModelTool
from bugbug.tools.bug_fix.bugzilla_mcp import BugzillaContext
from bugbug.tools.bug_fix.bugzilla_mcp import build_server as build_bugzilla_server
from bugbug.tools.duplicate_bugs.config import (
    BUGZILLA_READ_TOOLS,
    parse_dir_verdict,
    parse_verdict,
)

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #


@dataclass
class DuplicateResult:
    exit_code: int = 0
    results: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Transcript streaming
# --------------------------------------------------------------------------- #


def _truncate(s: str, n: int = 400) -> str:
    return s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"


class Reporter:
    def __init__(self, verbose: bool, log_path: Path | None):
        self.verbose = verbose
        self._log = log_path.open("w", encoding="utf-8") if log_path else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._log:
            self._log.close()

    def start_item(self, label: str) -> None:
        header = f"\n{'#' * 60}\n# {label}\n{'#' * 60}"
        self._emit(header, always=True)

    def _emit(self, line: str, *, always: bool = False, full: str | None = None):
        if self._log:
            self._log.write((full if full is not None else line) + "\n")
            self._log.flush()
        if always or self.verbose:
            print(line, file=sys.stderr)

    def message(self, msg) -> None:
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    self._emit(f"[agent] {block.text}", always=True)
                elif isinstance(block, ThinkingBlock):
                    thinking = block.thinking.strip()
                    self._emit(
                        f"[thinking] {_truncate(thinking.split(chr(10), 1)[0], 120)}",
                        full=f"[thinking]\n{thinking}",
                    )
                elif isinstance(block, ToolUseBlock):
                    inp = json.dumps(block.input, default=str)
                    self._emit(
                        f"[→tool] {block.name}({_truncate(inp, 200)})",
                        full=f"[→tool] {block.name}\n"
                        f"{json.dumps(block.input, indent=2, default=str)}",
                    )
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    marker = "ERR" if block.is_error else "ok"
                    if isinstance(block.content, str):
                        text = block.content
                    elif isinstance(block.content, list):
                        text = "\n".join(
                            c.get("text", "")
                            for c in block.content
                            if isinstance(c, dict) and c.get("type") == "text"
                        )
                    else:
                        text = str(block.content)
                    self._emit(
                        f"  [tool←{marker}] {_truncate(text, 300)}",
                        full=f"  [tool←{marker}]\n{text}",
                    )
        elif isinstance(msg, SystemMessage):
            if msg.subtype == "init":
                self._emit(
                    f"[system] session started (model={msg.data.get('model', '?')})"
                )
        elif isinstance(msg, ResultMessage):
            cost = f" cost=${msg.total_cost_usd:.4f}" if msg.total_cost_usd else ""
            self._emit(f"[done] turns={msg.num_turns}{cost}")
            if msg.is_error:
                self._emit(f"[done] ERROR: {msg.result}", always=True)


# --------------------------------------------------------------------------- #
# Agent sessions
# --------------------------------------------------------------------------- #


async def _run_session(
    options: ClaudeAgentOptions,
    prompt: str,
    reporter: Reporter,
) -> str:
    """Run one agent session to completion and extract its verdict."""
    final_text = ""
    errored = False
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            reporter.message(msg)
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        final_text = block.text
            elif isinstance(msg, ResultMessage):
                errored = msg.is_error

    if errored:
        return "ERROR"
    return parse_verdict(final_text) or "UNKNOWN"


async def match_local_crash(
    crash_path: Path,
    meta_bug: int,
    base_options: ClaudeAgentOptions,
    reporter: Reporter,
) -> str:
    opts = ClaudeAgentOptions(**{**base_options.__dict__, "cwd": str(crash_path)})

    contents = sorted(
        p.name + ("/" if p.is_dir() else "") for p in crash_path.iterdir()
    )
    prompt = (
        f"Crash directory: {crash_path}\n"
        f"Meta bug: {meta_bug}\n"
        f"Directory contents: {', '.join(contents) or '(empty)'}\n\n"
        f"Determine whether this crash is already filed as a blocker of "
        f"bug {meta_bug}. End your final response with the VERDICT: line."
    )
    return await _run_session(opts, prompt, reporter)


async def match_local_to_local(
    subject: Path,
    candidates: list[str],
    base_options: ClaudeAgentOptions,
    reporter: Reporter,
) -> str:
    contents = sorted(p.name + ("/" if p.is_dir() else "") for p in subject.iterdir())
    cand_lines = "\n".join(f"  - {c}" for c in candidates)
    prompt = (
        f"Subject directory: {subject.name}\n"
        f"Subject contents: {', '.join(contents) or '(empty)'}\n\n"
        f"Candidate directories ({len(candidates)}):\n{cand_lines}\n\n"
        f"Determine whether the subject crash matches any candidate. "
        f"End your final response with the VERDICT: line — either NEW "
        f"or exactly one of the candidate names above."
    )

    final_text = ""
    errored = False
    async with ClaudeSDKClient(options=base_options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            reporter.message(msg)
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        final_text = block.text
            elif isinstance(msg, ResultMessage):
                errored = msg.is_error

    if errored:
        return "ERROR"
    return parse_dir_verdict(final_text, set(candidates)) or "UNKNOWN"


async def match_filed_bug(
    subject: int,
    meta_bug: int,
    options: ClaudeAgentOptions,
    reporter: Reporter,
) -> str:
    prompt = (
        f"Subject bug: {subject}\n"
        f"Meta bug: {meta_bug}\n\n"
        f"Determine whether bug {subject} has a duplicate among the "
        f"blockers of bug {meta_bug}. End your final response with the "
        f"VERDICT: line."
    )
    return await _run_session(options, prompt, reporter)


# --------------------------------------------------------------------------- #
# Mode runners
# --------------------------------------------------------------------------- #


def _build_options(
    system_prompt: str,
    bugzilla_server,
    *,
    allow_local_fs: bool,
    model: str | None = None,
    max_turns: int | None = None,
) -> ClaudeAgentOptions:
    tools = list(BUGZILLA_READ_TOOLS)
    if allow_local_fs:
        tools = ["Read", "Glob", "Grep", *tools]
    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={"bugzilla": bugzilla_server},
        permission_mode="bypassPermissions",
        allowed_tools=tools,
        model=model,
        max_turns=max_turns,
        setting_sources=[],
    )


async def _run_local(
    *,
    local_dir: Path,
    meta_bug: int,
    bugzilla_server,
    model: str | None,
    max_turns: int | None,
    verbose: bool,
    log: Path | None,
) -> DuplicateResult:
    system_prompt = (
        (HERE / "prompts" / "dupdetector_local.md")
        .read_text()
        .format(meta_bug=meta_bug)
    )
    base_options = _build_options(
        system_prompt,
        bugzilla_server,
        allow_local_fs=True,
        model=model,
        max_turns=max_turns,
    )

    crash_subdirs = sorted(d for d in local_dir.iterdir() if d.is_dir())
    if not crash_subdirs:
        print(
            f"[duplicate_bugs] no sub-directories found in {local_dir}", file=sys.stderr
        )
        return DuplicateResult()

    print(
        f"[duplicate_bugs] matching {len(crash_subdirs)} crash(es) against "
        f"meta bug {meta_bug}",
        file=sys.stderr,
    )

    results: list[tuple[str, str]] = []
    exit_code = 0
    with Reporter(verbose=verbose, log_path=log) as reporter:
        for i, subdir in enumerate(crash_subdirs, 1):
            print(
                f"[duplicate_bugs] {i}/{len(crash_subdirs)}: {subdir.name}",
                file=sys.stderr,
            )
            reporter.start_item(f"crash: {subdir.name}")
            verdict = await match_local_crash(subdir, meta_bug, base_options, reporter)
            results.append((subdir.name, verdict))
            if verdict in ("ERROR", "UNKNOWN"):
                exit_code = 1

    return DuplicateResult(exit_code=exit_code, results=results)


async def _run_bugs(
    *,
    bug_ids: list[int],
    meta_bug: int,
    bugzilla_server,
    model: str | None,
    max_turns: int | None,
    verbose: bool,
    log: Path | None,
) -> DuplicateResult:
    print(
        f"[duplicate_bugs] checking {len(bug_ids)} bug(s) against blockers "
        f"of meta bug {meta_bug}",
        file=sys.stderr,
    )

    prompt_tmpl = (HERE / "prompts" / "dupdetector_bugs.md").read_text()

    results: list[tuple[str, str]] = []
    exit_code = 0
    with Reporter(verbose=verbose, log_path=log) as reporter:
        for i, subject in enumerate(bug_ids, 1):
            print(
                f"[duplicate_bugs] {i}/{len(bug_ids)}: bug {subject}", file=sys.stderr
            )
            reporter.start_item(f"bug {subject}")

            system_prompt = prompt_tmpl.format(subject=subject, meta_bug=meta_bug)
            options = _build_options(
                system_prompt,
                bugzilla_server,
                allow_local_fs=False,
                model=model,
                max_turns=max_turns,
            )

            verdict = await match_filed_bug(subject, meta_bug, options, reporter)
            if verdict.isdigit() and int(verdict) == subject:
                reporter._emit(
                    f"[duplicate_bugs] bug {subject}: verdict was itself — "
                    f"demoting to NEW",
                    always=True,
                )
                verdict = "NEW"
            results.append((str(subject), verdict))
            if verdict in ("ERROR", "UNKNOWN"):
                exit_code = 1

    return DuplicateResult(exit_code=exit_code, results=results)


async def _run_local_to_local(
    *,
    local_dir: Path,
    results_dir: Path,
    model: str | None,
    max_turns: int | None,
    verbose: bool,
    log: Path | None,
) -> DuplicateResult:
    crash_subdirs = sorted(d for d in local_dir.iterdir() if d.is_dir())
    if not crash_subdirs:
        print(
            f"[duplicate_bugs] no sub-directories found in {local_dir}", file=sys.stderr
        )
        return DuplicateResult()

    print(
        f"[duplicate_bugs] deduplicating {len(crash_subdirs)} crash(es) locally",
        file=sys.stderr,
    )

    system_prompt = (HERE / "prompts" / "dupdetector_local_to_local.md").read_text()
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        permission_mode="bypassPermissions",
        allowed_tools=["Read", "Glob", "Grep"],
        model=model,
        max_turns=max_turns,
        setting_sources=[],
        cwd=str(local_dir),
    )

    groups: dict[str, list[str]] = {}
    results: list[tuple[str, str]] = []
    exit_code = 0

    with Reporter(verbose=verbose, log_path=log) as reporter:
        for i, subdir in enumerate(crash_subdirs, 1):
            print(
                f"[duplicate_bugs] {i}/{len(crash_subdirs)}: {subdir.name}",
                file=sys.stderr,
            )

            representatives = list(groups.keys())
            if not representatives:
                verdict = "NEW"
            else:
                reporter.start_item(f"crash: {subdir.name}")
                verdict = await match_local_to_local(
                    subdir, representatives, options, reporter
                )
                if verdict == subdir.name:
                    verdict = "NEW"

            if verdict == "NEW":
                groups[subdir.name] = [subdir.name]
                rep = subdir.name
            elif verdict in ("ERROR", "UNKNOWN"):
                groups[subdir.name] = [subdir.name]
                rep = verdict
                exit_code = 1
            else:
                groups[verdict].append(subdir.name)
                rep = verdict

            results.append((subdir.name, rep))

    if results_dir is not None:
        results_dir.mkdir(parents=True)
        for rep_name in groups:
            shutil.copytree(local_dir / rep_name, results_dir / rep_name)

        print(
            f"[duplicate_bugs] {len(groups)} unique crash(es) copied to {results_dir}",
            file=sys.stderr,
        )

    return DuplicateResult(exit_code=exit_code, results=results)


# --------------------------------------------------------------------------- #
# Tool class
# --------------------------------------------------------------------------- #


class DuplicateBugsTool(GenerativeModelTool):
    """Duplicate bug detector using claude-agent-sdk."""

    @classmethod
    def create(cls, **kwargs):
        return cls()

    async def run(
        self,
        *,
        mode: str,
        base_url: str | None = None,
        api_key: str | None = None,
        meta_bug: int | None = None,
        bug_ids: list[int] | None = None,
        local_dir: Path | None = None,
        results_dir: Path | None = None,
        model: str | None = None,
        max_turns: int | None = None,
        verbose: bool = False,
        log: Path | None = None,
    ) -> DuplicateResult:
        if mode == "local_to_local":
            if local_dir is None:
                raise ValueError("local_dir is required for local_to_local mode")
            if results_dir is None:
                raise ValueError("results_dir is required for local_to_local mode")
            return await _run_local_to_local(
                local_dir=local_dir,
                results_dir=results_dir,
                model=model,
                max_turns=max_turns,
                verbose=verbose,
                log=log,
            )

        # Modes that need Bugzilla
        if not base_url or not api_key:
            raise ValueError("base_url and api_key are required for local/bugs modes")
        if meta_bug is None:
            raise ValueError("meta_bug is required for local/bugs modes")

        bz = bugsy.Bugsy(api_key=api_key, bugzilla_url=base_url)
        bz_ctx = BugzillaContext(client=bz, dry_run=True)
        bugzilla_server = build_bugzilla_server(bz_ctx)

        if mode == "local":
            if local_dir is None:
                raise ValueError("local_dir is required for local mode")
            return await _run_local(
                local_dir=local_dir,
                meta_bug=meta_bug,
                bugzilla_server=bugzilla_server,
                model=model,
                max_turns=max_turns,
                verbose=verbose,
                log=log,
            )
        elif mode == "bugs":
            if not bug_ids:
                raise ValueError("bug_ids is required for bugs mode")
            return await _run_bugs(
                bug_ids=bug_ids,
                meta_bug=meta_bug,
                bugzilla_server=bugzilla_server,
                model=model,
                max_turns=max_turns,
                verbose=verbose,
                log=log,
            )
        else:
            raise ValueError(
                f"Unknown mode: {mode}. Must be 'local', 'bugs', or 'local_to_local'"
            )
