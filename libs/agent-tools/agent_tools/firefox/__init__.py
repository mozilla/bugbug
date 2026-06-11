"""Firefox build + testcase-evaluation tools.

Framework-neutral ``@tool`` handlers over the implementations in ``.tools``;
each takes a :class:`FirefoxContext` (paths derived from the source repo) as its
first parameter and returns plain data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field

from agent_tools.registry import tool, tools_in

from .tools import bootstrap_firefox as _bootstrap_firefox
from .tools import build_firefox as _build_firefox
from .tools import evaluate_testcase as _evaluate_testcase
from .tools import js_shell_evaluator as _js_shell_evaluator


@dataclass
class FirefoxContext:
    """Firefox-related paths, derived from the source repo at startup.

    Defaults follow: mozconfig at the source root, objdir-ff-asan/ under it. The
    agent can still override the binary per-call if it wants to test a different
    build.
    """

    source_dir: Path
    mozconfig: Path
    objdir: Path
    binary: Path
    js_binary: Path

    @classmethod
    def from_source_repo(
        cls, source_repo: Path, objdir: str = "objdir-ff-asan"
    ) -> "FirefoxContext":
        src = source_repo.resolve()
        objdir_path = src / objdir
        return cls(
            source_dir=src,
            mozconfig=src / ".mozconfig",
            objdir=objdir_path,
            binary=objdir_path / "dist" / "bin" / "firefox",
            js_binary=objdir_path / "dist" / "bin" / "js",
        )


@tool
async def evaluate_testcase(
    ctx: FirefoxContext,
    content: Annotated[
        str, Field(description="Testcase file content (HTML, JS, SVG, etc.)")
    ],
    filename: Annotated[
        str,
        Field(
            description=(
                "Name for the testcase entry point, e.g. 'test.html'. Extension "
                "matters: grizzly serves it with the matching MIME type."
            )
        ),
    ],
    firefox_binary: Annotated[
        str | None,
        Field(
            description="Path to Firefox binary. Optional — defaults to the configured build's binary."
        ),
    ] = None,
    timeout: Annotated[
        int, Field(description="Seconds to wait for a crash (default: 30)")
    ] = 30,
    prefs: Annotated[
        dict[str, str | int | bool] | None,
        Field(
            description=(
                "Firefox about:config prefs to set before launch, e.g. "
                '{"dom.webgpu.enabled": true}. Use this to unlock gated features '
                "your testcase needs."
            )
        ),
    ] = None,
) -> dict:
    """Run a testcase in Firefox under xvfb and capture crash output via grizzly.

    The build's sanitizer configuration (ASAN, TSAN, plain debug, etc.) is
    whatever the configured mozconfig produces. Returns JSON: crashed (bool) —
    whether Firefox crashed; crashed_parent (bool) — parent process vs content
    process crash; logs (dict) — stderr/stdout and, if crashed, crashdata
    (crash/sanitizer report); files (dict) — the testcase bundle that triggered
    the crash; message (str) — human-readable summary. When crashed=false,
    logs.stderr/stdout often reveal why the trigger missed (JS exception, wrong
    pref, feature gated off).
    """
    binary = Path(firefox_binary or ctx.binary)
    return await _evaluate_testcase(
        content=content,
        filename=filename,
        firefox_binary=binary,
        timeout=timeout,
        prefs=prefs or {},
    )


@tool
async def build_firefox(
    ctx: FirefoxContext,
    firefox_dir: Annotated[
        str | None,
        Field(
            description="Firefox source directory. Optional — defaults to the configured source dir."
        ),
    ] = None,
    mozconfig_path: Annotated[
        str | None,
        Field(
            description="MOZCONFIG to use. Optional — defaults to the configured mozconfig."
        ),
    ] = None,
) -> dict:
    """Build Firefox using the configured mozconfig.

    Slow (tens of minutes on a cold build, faster incremental). Returns JSON:
    success (bool), build_dir (str), message (str), stdout/stderr. Only call this
    if you've changed source or the binary is missing — check if the binary
    exists first.
    """
    firefox_dir_p = Path(firefox_dir) if firefox_dir else ctx.source_dir
    mozconfig_p = Path(mozconfig_path) if mozconfig_path else ctx.mozconfig
    return await _build_firefox(firefox_dir_p, mozconfig_p, ctx.objdir)


@tool
async def evaluate_js_shell(
    ctx: FirefoxContext,
    content: Annotated[str, Field(description="JavaScript testcase source")],
    js_binary: Annotated[
        str | None,
        Field(
            description="Path to the SpiderMonkey js binary. Optional — defaults to the configured build's js shell."
        ),
    ] = None,
    timeout: Annotated[
        int,
        Field(description="Seconds to wait before killing the shell (default: 30)"),
    ] = 30,
    flags: Annotated[
        list[str] | None,
        Field(
            description=(
                'Extra shell flags, e.g. ["--no-threads", "--ion-eager"]. '
                "--fuzzing-safe is always prepended."
            )
        ),
    ] = None,
) -> dict:
    """Run a JS testcase in the SpiderMonkey shell and capture crash output.

    The shell's sanitizer configuration is whatever the configured mozconfig
    produces. Much faster than full-browser evaluate_testcase — use this for
    engine-level bugs (JIT, GC, TypedArrays, WASM) that don't need a DOM. Returns
    JSON: crashed (bool) — whether the shell crashed (signal or sanitizer);
    message (str) — human-readable summary, includes signal name if killed; logs
    (dict) — stderr/stdout (tail-truncated to 1 MB) and, if crashed, crashdata
    (crash/sanitizer report); files (dict) — the .js testcase that triggered the
    crash. A nonzero exit without a signal is a JS exception, NOT a crash — check
    logs.stderr for the syntax/runtime error.
    """
    binary = Path(js_binary or ctx.js_binary)
    return await _js_shell_evaluator(
        content=content, js_binary=binary, timeout=timeout, flags=flags
    )


@tool
async def bootstrap_firefox(
    ctx: FirefoxContext,
    firefox_dir: Annotated[
        str | None,
        Field(
            description="Firefox source directory. Optional — defaults to the configured source dir."
        ),
    ] = None,
) -> dict:
    """Run ``./mach bootstrap`` to install the Firefox build toolchain.

    Installs rust, clang, cbindgen under the running user's ~/.mozbuild/.
    Required before a full (non-artifact) build. Slow — ~10-15 min on a fresh
    image, fast on re-runs. Returns JSON: success, message, stdout, stderr. Only
    call this if you intend to do a full build; artifact builds don't need
    bootstrap.
    """
    firefox_dir_p = Path(firefox_dir) if firefox_dir else ctx.source_dir
    return await _bootstrap_firefox(firefox_dir_p)


TOOLS = tools_in(__name__)
