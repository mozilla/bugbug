"""Bug fix triage tool -- a Bugzilla triage agent.

Orchestrates a Claude agent that triages bugs according to rulesets
in the rules/ directory, with access to a source repository and an
in-process Bugzilla MCP server.

Adapted from the standalone larrey project with minimal changes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import bugsy
from claude_agent_sdk import (
    AgentDefinition,
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
from bugbug.tools.bug_fix.config import (
    BUGZILLA_READ_TOOLS,
    BUGZILLA_WRITE_TOOLS,
    FIREFOX_TOOLS,
    SOURCE_WRITE_TOOLS,
)
from bugbug.tools.bug_fix.firefox_mcp import FirefoxContext
from bugbug.tools.bug_fix.firefox_mcp import build_server as build_firefox_server

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #


@dataclass
class TriageResult:
    exit_code: int = 0
    bugs_processed: int = 0
    simulated_writes: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Bug selection
# --------------------------------------------------------------------------- #


def fetch_initial_bugs(
    bz: bugsy.Bugsy,
    bug_ids: list[int] | None,
    keywords: list[str],
    blocks: int | None,
    status: list[str],
) -> tuple[list[int], list[int]]:
    """Resolve selectors into a concrete list of bug IDs.

    All selectors intersect: ``bugs=[1,2,3]`` + ``keywords=["sec-low"]``
    returns only those of 1,2,3 that carry sec-low.

    Returns (selected_ids, inaccessible_ids).
    """
    params: dict = {"include_fields": "id"}
    if bug_ids:
        params["id"] = ",".join(str(i) for i in bug_ids)
    for kw in keywords:
        params.setdefault("keywords", []).append(kw)
    if blocks is not None:
        params["blocks"] = blocks
    if status:
        params["status"] = status

    if not bug_ids:
        params.setdefault("limit", 200)

    for k, v in list(params.items()):
        if isinstance(v, list) and len(v) == 1:
            params[k] = v[0]

    result = bz.request("bug", params=params)
    returned = [b["id"] for b in result.get("bugs", [])]
    returned_set = set(returned)

    inaccessible: list[int] = []
    if bug_ids:
        inaccessible = [i for i in bug_ids if i not in returned_set]

    return returned, inaccessible


# --------------------------------------------------------------------------- #
# Prompts & agents
# --------------------------------------------------------------------------- #


def load_system_prompt(
    rules_dir: Path,
    extra: str,
    dry_run: bool,
) -> str:
    tmpl = (HERE / "prompts" / "system.md").read_text()
    if dry_run:
        mode = (
            "**DRY-RUN ACTIVE.** Bugzilla write tools (`update_bug`, "
            "`add_comment`, `add_attachment`, `create_bug`) are disabled — "
            "do not attempt them. Source-repo edits (Write/Edit) are still "
            "allowed so you can prepare and inspect a candidate patch; the "
            "caller will review diffs manually."
        )
    else:
        mode = "Writes are live. Be careful."
    return tmpl.format(
        rules_dir=str(rules_dir.resolve()),
        extra_instructions=extra or "(none)",
        run_mode_note=mode,
    )


def make_investigator() -> AgentDefinition:
    """Create a single generic investigator subagent definition."""
    return AgentDefinition(
        description=(
            "Focused investigator for answering a specific question about "
            "a bug or the source tree. The main agent writes your complete "
            "instructions at spawn time — follow them precisely and return "
            "only what was asked for."
        ),
        prompt=(
            "You are a focused investigator subagent. You will be given a "
            "self-contained task by the triage agent. Complete it and return "
            "a concise answer. Do not make Bugzilla modifications — you have "
            "read-only access. Do not speculate beyond what you can verify."
        ),
        tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            *BUGZILLA_READ_TOOLS,
            *FIREFOX_TOOLS,
        ],
        model="inherit",
    )


# --------------------------------------------------------------------------- #
# Output streaming
# --------------------------------------------------------------------------- #


def _truncate(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"


class Reporter:
    """Routes streamed agent messages to stdout and/or a log file."""

    def __init__(self, verbose: bool, log_path: Path | None):
        self.verbose = verbose
        self._log = log_path.open("w", encoding="utf-8") if log_path else None
        self._turn = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._log:
            self._log.close()

    def start_bug(self, bug_id: int) -> None:
        self._turn = 0
        header = f"\n{'#' * 60}\n# bug {bug_id}\n{'#' * 60}"
        self._emit(header, always=True)

    def _emit(self, line: str, *, always: bool = False, full: str | None = None):
        if self._log:
            self._log.write((full if full is not None else line) + "\n")
            self._log.flush()
        if always or self.verbose:
            print(line)

    def message(self, msg) -> None:
        if isinstance(msg, AssistantMessage):
            is_main = msg.parent_tool_use_id is None
            label = "agent" if is_main else "subagent"
            if is_main:
                self._turn += 1
                self._emit(f"\n--- turn {self._turn} ---")
            for block in msg.content:
                if isinstance(block, TextBlock):
                    self._emit(f"\n[{label}] {block.text}", always=is_main)
                elif isinstance(block, ThinkingBlock):
                    thinking = block.thinking.strip()
                    snippet = thinking.split("\n", 1)[0]
                    self._emit(
                        f"[{label}:thinking] {_truncate(snippet, 120)}",
                        full=f"[{label}:thinking]\n{thinking}",
                    )
                elif isinstance(block, ToolUseBlock):
                    inp = json.dumps(block.input, default=str)
                    inp_full = json.dumps(block.input, indent=2, default=str)
                    self._emit(
                        f"[{label}→tool] {block.name}({_truncate(inp, 300)})",
                        full=f"[{label}→tool] {block.name}\n{inp_full}",
                    )

        elif isinstance(msg, UserMessage):
            if isinstance(msg.content, list):
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        marker = "ERROR" if block.is_error else "ok"
                        if isinstance(block.content, str):
                            text = block.content
                        elif isinstance(block.content, list):
                            parts = [
                                c.get("text", "")
                                for c in block.content
                                if isinstance(c, dict) and c.get("type") == "text"
                            ]
                            text = "\n".join(parts)
                        else:
                            text = str(block.content)
                        self._emit(
                            f"  [tool←{marker}] {_truncate(text, 400)}",
                            full=f"  [tool←{marker}]\n{text}",
                        )

        elif isinstance(msg, SystemMessage):
            if msg.subtype == "init":
                model = msg.data.get("model", "?")
                self._emit(f"[system] session started (model={model})")
            else:
                data = json.dumps(msg.data, default=str)
                self._emit(
                    f"[system:{msg.subtype}] {_truncate(data, 200)}",
                    full=f"[system:{msg.subtype}] {data}",
                )

        elif isinstance(msg, ResultMessage):
            self._emit(f"\n{'=' * 60}", always=True)
            if msg.total_cost_usd:
                line = f"[done] turns={msg.num_turns} cost=${msg.total_cost_usd:.4f}"
            else:
                line = f"[done] turns={msg.num_turns}"
            self._emit(line, always=True)
            if msg.is_error:
                self._emit(f"[done] ERROR: {msg.result}", always=True)


# --------------------------------------------------------------------------- #
# Tool class
# --------------------------------------------------------------------------- #


class BugFixTool(GenerativeModelTool):
    """Bugzilla triage agent using claude-agent-sdk."""

    @classmethod
    def create(cls, **kwargs):
        return cls()

    async def run(
        self,
        *,
        base_url: str,
        api_key: str,
        source_repo: Path,
        bugs: list[int] | None = None,
        keywords: list[str] | None = None,
        blocks: int | None = None,
        status: list[str] | None = None,
        instructions: str = "",
        task: str | None = None,
        rules_dir: Path | None = None,
        dry_run: bool = False,
        newest_first: bool = False,
        model: str | None = None,
        max_turns: int | None = None,
        effort: str | None = None,
        verbose: bool = False,
        log: Path | None = None,
    ) -> TriageResult:
        if rules_dir is None:
            rules_dir = HERE / "rules"
        keywords = keywords or []
        status = status or []

        # --- Bugzilla client & MCP server --------------------------------- #
        bz = bugsy.Bugsy(api_key=api_key, bugzilla_url=base_url)
        bz_ctx = BugzillaContext(client=bz, dry_run=dry_run)
        bugzilla_server = build_bugzilla_server(bz_ctx)

        # --- Firefox build/eval MCP server -------------------------------- #
        fx_ctx = FirefoxContext.from_source_repo(source_repo)
        firefox_server = build_firefox_server(fx_ctx)

        # --- Resolve bug selectors to concrete IDs ------------------------ #
        print("[bug_fix] resolving bug set...", file=sys.stderr)
        try:
            selected, inaccessible = fetch_initial_bugs(
                bz, bugs, keywords, blocks, status
            )
        except bugsy.BugsyException as e:
            print(f"[bug_fix] bug selection failed: {e}", file=sys.stderr)
            return TriageResult(exit_code=2)

        if inaccessible:
            print(
                f"[bug_fix] {len(inaccessible)} bug(s) inaccessible, skipping: {inaccessible}",
                file=sys.stderr,
            )
        if not selected:
            print(
                "[bug_fix] no accessible bugs match the selectors — nothing to do",
                file=sys.stderr,
            )
            return TriageResult(exit_code=0)

        selected.sort(reverse=newest_first)
        print(f"[bug_fix] triaging {len(selected)} bug(s): {selected}", file=sys.stderr)

        # --- Build agent options ------------------------------------------ #
        system_prompt = load_system_prompt(rules_dir, instructions, dry_run)

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={"bugzilla": bugzilla_server, "firefox": firefox_server},
            agents={"investigator": make_investigator()},
            cwd=str(source_repo.resolve()),
            add_dirs=[str(rules_dir.resolve())],
            permission_mode="bypassPermissions",
            allowed_tools=[
                "Read",
                "Grep",
                "Glob",
                "Bash",
                "Task",
                *SOURCE_WRITE_TOOLS,
                *BUGZILLA_READ_TOOLS,
                *BUGZILLA_WRITE_TOOLS,
                *FIREFOX_TOOLS,
            ],
            disallowed_tools=list(BUGZILLA_WRITE_TOOLS) if dry_run else [],
            model=model,
            max_turns=max_turns,
            **({"effort": effort} if effort else {}),
            setting_sources=[],
        )

        # --- Run: one fresh agent context per bug ------------------------- #
        exit_code = 0
        rules_path = rules_dir.resolve()
        with Reporter(verbose=verbose, log_path=log) as reporter:
            for i, bug_id in enumerate(selected, 1):
                print(f"[bug_fix] bug {i}/{len(selected)}: {bug_id}", file=sys.stderr)
                reporter.start_bug(bug_id)

                if task:
                    user_prompt = (
                        f"Bug to work on: {bug_id}\n\n"
                        f"Task: {task}\n\n"
                        f"The rules in {rules_path} are available if the task "
                        f"calls for them, but the task above is your primary "
                        f"directive — it overrides the default triage workflow."
                    )
                else:
                    user_prompt = (
                        f"Triage bug {bug_id}.\n\n"
                        f"Consult the relevant rules in {rules_path}."
                    )

                async with ClaudeSDKClient(options=options) as client:
                    await client.query(user_prompt)
                    async for msg in client.receive_response():
                        reporter.message(msg)
                        if isinstance(msg, ResultMessage) and msg.is_error:
                            exit_code = 1

        # --- Build result ------------------------------------------------- #
        result = TriageResult(
            exit_code=exit_code,
            bugs_processed=len(selected),
            simulated_writes=list(bz_ctx.simulated) if dry_run else [],
        )

        if dry_run and bz_ctx.simulated:
            print(f"\n{'=' * 60}", file=sys.stderr)
            print(
                f"[bug_fix] DRY-RUN SUMMARY: {len(bz_ctx.simulated)} simulated write(s)",
                file=sys.stderr,
            )
            for i, s in enumerate(bz_ctx.simulated, 1):
                print(f"  {i}. {s['action']} bug {s['bug_id']}", file=sys.stderr)

        return result
