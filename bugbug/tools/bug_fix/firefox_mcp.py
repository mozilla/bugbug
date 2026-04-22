"""In-process MCP server for Firefox build + testcase evaluation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from bugbug.tools.bug_fix.firefox_tools import (
    build_firefox,
    evaluate_testcase,
    js_shell_evaluator,
)


@dataclass
class FirefoxContext:
    """Firefox-related paths, derived from --source-repo at startup.

    Defaults follow: mozconfig at the source root, objdir-ff-asan/ under it. The
    agent can still override firefox_binary per-call if it wants to test a
    different build.
    """

    source_dir: Path
    mozconfig: Path
    objdir: Path
    binary: Path
    js_binary: Path

    @classmethod
    def from_source_repo(cls, source_repo: Path) -> "FirefoxContext":
        src = source_repo.resolve()
        objdir = src / "objdir-ff-asan"
        return cls(
            source_dir=src,
            mozconfig=src / ".mozconfig",
            objdir=objdir,
            binary=objdir / "dist" / "bin" / "firefox",
            js_binary=objdir / "dist" / "bin" / "js",
        )


def _jtext(obj) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(obj, indent=2)}]}


def build_server(ctx: FirefoxContext):
    """Create the in-process Firefox MCP server bound to ``ctx``."""

    @tool(
        "evaluate_testcase",
        "Run a testcase in an ASAN-instrumented Firefox under xvfb and "
        "capture crash output via grizzly. Returns JSON: "
        "crashed (bool) — whether Firefox crashed; "
        "crashed_parent (bool) — parent process vs content process crash; "
        "logs (dict) — stderr/stdout and, if crashed, crashdata (ASAN report); "
        "files (dict) — the testcase bundle that triggered the crash; "
        "message (str) — human-readable summary. "
        "When crashed=false, logs.stderr/stdout often reveal why the trigger "
        "missed (JS exception, wrong pref, feature gated off).",
        {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Testcase file content (HTML, JS, SVG, etc.)",
                },
                "filename": {
                    "type": "string",
                    "description": (
                        "Name for the testcase entry point, e.g. 'test.html'. "
                        "Extension matters: grizzly serves it with the matching "
                        "MIME type."
                    ),
                },
                "firefox_binary": {
                    "type": "string",
                    "description": (
                        "Path to Firefox binary. Optional — defaults to the "
                        f"ASAN build at {ctx.binary}"
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Seconds to wait for a crash (default: 30)",
                },
                "prefs": {
                    "type": "object",
                    "description": (
                        "Firefox about:config prefs to set before launch, e.g. "
                        '{"dom.webgpu.enabled": true}. Use this to unlock '
                        "gated features your testcase needs."
                    ),
                    "additionalProperties": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "integer"},
                            {"type": "boolean"},
                        ]
                    },
                },
            },
            "required": ["content", "filename"],
        },
    )
    async def evaluate_testcase_tool(args):
        binary = Path(args.get("firefox_binary") or ctx.binary)
        crash_info = await evaluate_testcase(
            content=args["content"],
            filename=args["filename"],
            firefox_binary=binary,
            timeout=args.get("timeout", 30),
            prefs=args.get("prefs") or {},
        )
        return _jtext(crash_info)

    @tool(
        "build_firefox",
        "Build Firefox with the ASAN+UBSAN fuzzing mozconfig. Slow (tens of "
        "minutes on a cold build, faster incremental). Returns JSON: "
        "success (bool), build_dir (str), message (str), stdout/stderr. "
        "Only call this if you've changed source or the binary is missing — "
        "check if the binary exists first.",
        {
            "type": "object",
            "properties": {
                "firefox_dir": {
                    "type": "string",
                    "description": (
                        "Firefox source directory. Optional — defaults to "
                        f"{ctx.source_dir}"
                    ),
                },
                "mozconfig_path": {
                    "type": "string",
                    "description": (
                        f"MOZCONFIG to use. Optional — defaults to {ctx.mozconfig}"
                    ),
                },
            },
        },
    )
    async def build_firefox_tool(args):
        firefox_dir = (
            Path(args["firefox_dir"]) if "firefox_dir" in args else ctx.source_dir
        )
        mozconfig = (
            Path(args["mozconfig_path"]) if "mozconfig_path" in args else ctx.mozconfig
        )
        result = await build_firefox(firefox_dir, mozconfig, ctx.objdir)
        return _jtext(result)

    @tool(
        "evaluate_js_shell",
        "Run a JS testcase in the SpiderMonkey shell (ASAN build) and "
        "capture crash output. Much faster than full-browser evaluate_testcase "
        "— use this for engine-level bugs (JIT, GC, TypedArrays, WASM) that "
        "don't need a DOM. Returns JSON: "
        "crashed (bool) — whether the shell crashed (signal or ASAN); "
        "message (str) — human-readable summary, includes signal name if killed; "
        "logs (dict) — stderr/stdout (tail-truncated to 1 MB) and, if crashed, "
        "crashdata (ASAN report); "
        "files (dict) — the .js testcase that triggered the crash. "
        "A nonzero exit without a signal is a JS exception, NOT a crash — "
        "check logs.stderr for the syntax/runtime error.",
        {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "JavaScript testcase source",
                },
                "js_binary": {
                    "type": "string",
                    "description": (
                        "Path to the SpiderMonkey js binary. Optional — "
                        f"defaults to {ctx.js_binary}"
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Seconds to wait before killing the shell (default: 30)",
                },
                "flags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        'Extra shell flags, e.g. ["--no-threads", '
                        '"--ion-eager"]. --fuzzing-safe is always prepended.'
                    ),
                },
            },
            "required": ["content"],
        },
    )
    async def evaluate_js_shell_tool(args):
        binary = Path(args.get("js_binary") or ctx.js_binary)
        crash_info = await js_shell_evaluator(
            content=args["content"],
            js_binary=binary,
            timeout=args.get("timeout", 30),
            flags=args.get("flags"),
        )
        return _jtext(crash_info)

    return create_sdk_mcp_server(
        name="firefox",
        version="0.1.0",
        tools=[evaluate_testcase_tool, build_firefox_tool, evaluate_js_shell_tool],
    )
