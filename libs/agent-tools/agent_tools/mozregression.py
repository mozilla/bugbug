"""Run a mozregression bisection as an agent tool.

Wraps the ``mozregression`` CLI so an agent can bisect a Firefox regression and
get back a changeset/pushlog range. The verdict for each candidate build is
produced by mozregression's ``--prompt`` mode (mozilla/mozregression#2197): the
tool hands mozregression a natural-language good/bad instruction, and
mozregression drives the Firefox DevTools MCP (via the Claude CLI) to classify
each build -- the natural-language equivalent of ``git bisect run``.

The CLI, the ``claude`` binary, ``npx`` and the Firefox DevTools MCP are provided
by the agent's container image (not a Python dependency here). Bisection is slow
and downloads real builds, so the handler is a long-running subprocess wrapper
that -- like ``firefox.build_firefox`` -- ALWAYS returns a structured dict and
never raises: the agent inspects ``success`` / ``message`` and the parsed range.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field

from agent_tools.registry import tool, tools_in

# mozregression prints the final range near the end of its output. The wording
# depends on the mode: a regression bisection reports ``Last good revision`` /
# ``First bad revision``, while ``--find-fix`` reports ``First good revision`` /
# ``Last bad revision``. It also prints several intermediate ``Pushlog:`` URLs as
# it narrows, so we take the LAST one (the final range) rather than the first.
_GOOD_RE = re.compile(r"(?:Last|First) good revision:\s*([0-9a-fA-F]{7,40})")
_BAD_RE = re.compile(r"(?:First|Last) bad revision:\s*([0-9a-fA-F]{7,40})")
_PUSHLOG_RE = re.compile(r"(https?://\S*pushlog\S*)")
_PUSHLOG_RANGE_RE = re.compile(
    r"fromchange=([0-9a-fA-F]+)&(?:amp;)?tochange=([0-9a-fA-F]+)"
)
_REGRESSOR_BUG_RE = re.compile(r"show_bug\.cgi\?id=(\d+)")


def _parse_range(text: str) -> dict:
    """Scrape the final good/bad changesets and pushlog URL from CLI output.

    Handles both regression (``Last good`` / ``First bad``) and ``--find-fix``
    (``First good`` / ``Last bad``) wording, and always takes the last pushlog
    URL. Falls back to the pushlog URL's ``fromchange``/``tochange`` when the
    labelled revision lines are absent. Returns keys ``last_good``, ``first_bad``,
    ``pushlog_url`` (all ``None`` when not found).
    """
    good = _GOOD_RE.findall(text)
    bad = _BAD_RE.findall(text)
    pushlogs = _PUSHLOG_RE.findall(text)
    pushlog_url = pushlogs[-1] if pushlogs else None

    last_good = good[-1] if good else None
    first_bad = bad[-1] if bad else None

    if (last_good is None or first_bad is None) and pushlog_url:
        m = _PUSHLOG_RANGE_RE.search(pushlog_url)
        if m:
            # Pushlog range is fromchange=<older>&tochange=<newer>; for a
            # regression <older> is the last good and <newer> the first bad. For
            # --find-fix the roles invert (older=last bad, newer=first good), but
            # the labelled lines above cover that case, so this fallback only
            # fires for a plain regression range.
            last_good = last_good or m.group(1)
            first_bad = first_bad or m.group(2)

    return {
        "last_good": last_good,
        "first_bad": first_bad,
        "pushlog_url": pushlog_url,
    }


@dataclass
class MozregressionContext:
    """Configuration for driving the mozregression CLI.

    ``anthropic_api_key`` is injected into the subprocess environment so the
    nested ``claude`` CLI that mozregression's ``--prompt`` mode spawns can
    authenticate; the agent process's own key is reused. No other credentials
    are needed (build downloads and HGMO are public).
    """

    anthropic_api_key: str | None = None
    app: str = "firefox"
    default_model: str | None = None
    headless: bool = True
    # Bisection downloads and tests many builds; allow a very large ceiling.
    timeout: float = 3 * 60 * 60
    # Cap captured output so a chatty run can't blow up the agent's context.
    max_output_bytes: int = 40_000
    executable: str = "mozregression"


def _tail(text: str, limit: int) -> str:
    """Keep the last ``limit`` bytes of output (the range is printed at the end)."""
    raw = text.encode("utf-8", errors="ignore")
    if len(raw) <= limit:
        return text
    return "...<truncated>...\n" + raw[-limit:].decode("utf-8", errors="ignore")


@tool
async def run_mozregression(
    ctx: MozregressionContext,
    good: Annotated[
        str,
        Field(
            description=(
                "The last known-good bound: a date (YYYY-MM-DD), a Firefox "
                "version number (e.g. '123'), or a changeset hash. Must be older "
                "than 'bad' for a regression."
            )
        ),
    ],
    bad: Annotated[
        str,
        Field(
            description=(
                "The first known-bad bound: a date (YYYY-MM-DD), a Firefox "
                "version number, or a changeset hash."
            )
        ),
    ],
    prompt: Annotated[
        str,
        Field(
            description=(
                "Natural-language good/bad instruction driving the Firefox "
                "DevTools MCP, in a form that yields a clear verdict, e.g. "
                "'Navigate to <url> and do X. GOOD if <baseline behavior>, "
                "BAD if <broken behavior>.'"
            )
        ),
    ],
    url: Annotated[
        str | None,
        Field(
            description=(
                "Optional URL to open the build on (passed to Firefox via "
                "'--arg'). Include it here when the check needs a specific page."
            )
        ),
    ] = None,
    prefs: Annotated[
        dict[str, str] | None,
        Field(
            description=(
                "Optional Firefox preferences to set for every candidate build, "
                "as {pref_name: value} (passed as '--pref name:value')."
            )
        ),
    ] = None,
    app: Annotated[
        str | None,
        Field(description="Application to bisect; defaults to 'firefox'."),
    ] = None,
    repo: Annotated[
        str | None,
        Field(
            description=(
                "Optional integration branch to bisect (e.g. 'autoland'); "
                "defaults to mozregression's own choice."
            )
        ),
    ] = None,
    find_fix: Annotated[
        bool,
        Field(
            description=(
                "Set true to bisect for a fix instead of a regression (reverses "
                "the good/bad ordering)."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Bisect a Firefox regression with mozregression and return the range.

    Runs mozregression in ``--prompt`` mode between ``good`` and ``bad``, using
    the natural-language ``prompt`` to classify each build. Returns a dict with
    ``success``, the parsed ``last_good`` / ``first_bad`` changesets,
    ``pushlog_url``, a ``regressor_bug`` if mozregression mapped the range to a
    single bug, and ``stdout`` / ``stderr`` tails. Always returns a dict; never
    raises. Bisection is slow (it downloads and tests many builds).
    """
    argv: list[str] = [
        ctx.executable,
        "--app",
        app or ctx.app,
        "--good",
        good,
        "--bad",
        bad,
        "--prompt",
        prompt,
    ]
    if ctx.headless:
        argv.append("--prompt-headless")
    if ctx.default_model:
        argv += ["--prompt-model", ctx.default_model]
    if url:
        argv += ["--arg", url]
    for name, value in (prefs or {}).items():
        argv += ["--pref", f"{name}:{value}"]
    if repo:
        argv += ["--repo", repo]
    if find_fix:
        argv.append("--find-fix")

    env = os.environ.copy()
    if ctx.anthropic_api_key:
        env["ANTHROPIC_API_KEY"] = ctx.anthropic_api_key

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "message": (
                f"mozregression executable '{ctx.executable}' not found on PATH"
            ),
        }

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(), timeout=ctx.timeout
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return {
            "success": False,
            "message": f"mozregression timed out after {ctx.timeout:.0f}s",
        }

    stdout = stdout_b.decode("utf-8", errors="ignore") if stdout_b else ""
    stderr = stderr_b.decode("utf-8", errors="ignore") if stderr_b else ""
    combined = f"{stdout}\n{stderr}"

    parsed = _parse_range(combined)
    regressor_bug = _REGRESSOR_BUG_RE.search(combined)

    resolved = {
        "success": process.returncode == 0,
        "returncode": process.returncode,
        "last_good": parsed["last_good"],
        "first_bad": parsed["first_bad"],
        "pushlog_url": parsed["pushlog_url"],
        "regressor_bug": int(regressor_bug.group(1)) if regressor_bug else None,
        "command": argv[:1] + ["--app", app or ctx.app, "--good", good, "--bad", bad],
        "stdout": _tail(stdout, ctx.max_output_bytes),
        "stderr": _tail(stderr, ctx.max_output_bytes),
    }
    if resolved["success"]:
        resolved["message"] = "mozregression completed"
    else:
        resolved["message"] = (
            f"mozregression exited with code {process.returncode}; "
            "the range may be incomplete"
        )
    return resolved


TOOLS = tools_in(__name__)
