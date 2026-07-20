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

Neither the builds nor the per-build ``claude`` judge get the network. Bounds and
directives come from bug text anyone can write, so an open build would let a bug
aim our infrastructure at any site. The judge loses every built-in tool here
(Bash and WebFetch above all), keeping only the DevTools MCP it drives the build
through. The builds are locked by an enterprise policy the agent image installs
rather than by prefs passed here, because a locked policy is one neither
``prefs`` nor the judge (through the MCP) can turn back off.
mozregression's own downloads are unaffected: it picks builds by version, date or
changeset from Mozilla's archive and HGMO, never by URL.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field

from agent_tools.registry import tool, tools_in

# mozregression prints the final range near the end of its output as ``Last good
# revision`` / ``First bad revision``. It also prints several intermediate
# ``Pushlog:`` URLs as it narrows, so we take the LAST one (the final range)
# rather than the first.
_GOOD_RE = re.compile(r"Last good revision:\s*([0-9a-fA-F]{7,40})")
_BAD_RE = re.compile(r"First bad revision:\s*([0-9a-fA-F]{7,40})")
_PUSHLOG_RE = re.compile(r"(https?://\S*pushlog\S*)")
_PUSHLOG_RANGE_RE = re.compile(
    r"fromchange=([0-9a-fA-F]+)&(?:amp;)?tochange=([0-9a-fA-F]+)"
)


def _parse_range(text: str) -> dict:
    """Scrape the final good/bad changesets and pushlog URL from CLI output.

    Always takes the last pushlog URL. Falls back to its ``fromchange`` /
    ``tochange`` when the labelled revision lines are absent. Returns keys
    ``last_good``, ``first_bad``, ``pushlog_url`` (all ``None`` when not found).
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
            # Pushlog range is fromchange=<last good>&tochange=<first bad>.
            last_good = last_good or m.group(1)
            first_bad = first_bad or m.group(2)

    return {
        "last_good": last_good,
        "first_bad": first_bad,
        "pushlog_url": pushlog_url,
    }


# Deny rules hold under the judge's `--permission-mode bypassPermissions`, which is
# what makes this work without changing mozregression: a denied tool is not offered
# at all. Read/Glob/Grep go too, since the judge has no use for files and the
# process environment holds the Anthropic key.
_JUDGE_SETTINGS = {
    "permissions": {
        "deny": [
            "Bash",
            "WebFetch",
            "WebSearch",
            "Read",
            "Glob",
            "Grep",
            "Write",
            "Edit",
            "NotebookEdit",
            "Task",
        ]
    }
}


@dataclass
class MozregressionContext:
    """Configuration for driving the mozregression CLI.

    The nested ``claude`` CLI that ``--prompt`` mode spawns authenticates with the
    ``ANTHROPIC_API_KEY`` it inherits from this process. No other credentials are
    needed (build downloads and HGMO are public).
    """

    default_model: str | None = None
    # Bisection downloads and tests many builds; allow a very large ceiling.
    timeout: float = 3 * 60 * 60
    # Cap captured output so a chatty run can't blow up the agent's context.
    max_output_bytes: int = 40_000


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
                "Optional page to open the build on (passed to Firefox via "
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
    repo: Annotated[
        str | None,
        Field(
            description=(
                "Repository the good/bad changesets belong to, e.g. 'autoland'. "
                "Required when a bound is an integration changeset, such as the "
                "last_good/first_bad of an earlier run; omit for versions and dates."
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """Bisect a Firefox regression with mozregression and return the range.

    Runs mozregression in ``--prompt`` mode between ``good`` and ``bad``, using
    the natural-language ``prompt`` to classify each build. Returns a dict with
    ``success``, the parsed ``last_good`` / ``first_bad`` changesets,
    ``pushlog_url``, and ``stdout`` / ``stderr`` tails. Always returns a dict; never
    raises. Bisection is slow (it downloads and tests many builds).
    """
    argv: list[str] = [
        "mozregression",
        "--app",
        "firefox",
        "--good",
        good,
        "--bad",
        bad,
        "--prompt",
        prompt,
        "--prompt-headless",
    ]
    if ctx.default_model:
        argv += ["--prompt-model", ctx.default_model]
    if url:
        argv += ["--arg", url]
    for name, value in (prefs or {}).items():
        argv += ["--pref", f"{name}:{value}"]
    if repo:
        argv += ["--repo", repo]

    with tempfile.TemporaryDirectory(prefix="mozregression-judge-") as judge_config:
        with open(os.path.join(judge_config, "settings.json"), "w") as f:
            json.dump(_JUDGE_SETTINGS, f)
        # Every `claude` mozregression starts inherits this, and only those do: the
        # agent running this tool keeps its own configuration.
        env = {**os.environ, "CLAUDE_CONFIG_DIR": judge_config}

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
                "message": "mozregression executable not found on PATH",
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

    resolved = {
        "success": process.returncode == 0,
        "returncode": process.returncode,
        "last_good": parsed["last_good"],
        "first_bad": parsed["first_bad"],
        "pushlog_url": parsed["pushlog_url"],
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
