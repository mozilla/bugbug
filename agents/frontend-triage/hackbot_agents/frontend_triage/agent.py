"""Frontend triage tool -- a read-only Bugzilla triage + fix-planning agent.

Orchestrates a Claude agent that triages user-facing Firefox bugs -- the desktop
frontend, Firefox for Android, and the Windows installer and application updater --
according to rulesets in the rules/ directory. The agent investigates the
source repository READ-ONLY (no build, no source edits, no reproduction) and
produces a root-cause analysis plus a proposed fix plan, which it records as a
Bugzilla comment for a human (or a downstream execution agent) to act on.

It reaches Bugzilla via an out-of-process MCP broker (HTTP transport) that holds
the Bugzilla token -- the agent process itself never sees it.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from agent_tools import mozilla_vcs, searchfox
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.mozilla_vcs import MozillaVcsContext
from agent_tools.searchfox import SearchfoxContext
from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpServerConfig,
    ResultMessage,
)
from hackbot_runtime import ActionsRecorder, AgentError, HackbotAgentResult
from hackbot_runtime.actions import ACTIONS_SERVER_NAME
from hackbot_runtime.actions.claude_sdk import actions_server_for, actions_to_tool_names
from hackbot_runtime.claude import Reporter
from hackbot_runtime.searchfox import (
    PLACEHOLDER as SEARCHFOX_PLACEHOLDER,
)
from hackbot_runtime.searchfox import (
    permalink_hook,
    permalink_prefix,
    resolve_index_revision,
)
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel
from searchfox import AsyncSearchfoxClient

from . import areas as area_tools
from .areas import AreaGuidanceContext
from .config import (
    AREA_TOOLS,
    AREAS,
    BUGZILLA_READ_TOOLS,
    ENABLED_ACTION_TYPES,
    MOZILLA_VCS_TOOLS,
    SEARCHFOX_TOOLS,
    TRIAGE_SCOPE,
    TRIAGE_SEVERITIES,
    Area,
    ScopedComponent,
    areas_for,
)
from .hooks import add_comment_hook, area_guidance_hook, severity_block_hook

HERE = Path(__file__).resolve().parent
AREAS_DIR = HERE / "rules" / "areas"

# The agent is asked to end its final message with a fenced ```json block
# carrying the structured plan. We parse the last such block so the result is
# machine-consumable for downstream handoff (summary.json -> execution agent).
_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)

_FEEDBACK_TAGS = (
    "If you want to categorize your feedback you can add one of the following "
    "tags: ai-triage-wrong-file, ai-triage-wrong-cause, ai-triage-hallucination, "
    "ai-triage-out-of-scope."
)


def feedback_tags_hook(action: dict) -> None:
    """Offer the triage-specific feedback tags below the runtime's footer."""
    params = action.get("params")
    if not isinstance(params, dict):
        return
    text = params.get("text")
    if isinstance(text, str):
        params["text"] = f"{text.rstrip()}\n{_FEEDBACK_TAGS}"


class SeverityAssessment(BaseModel):
    """Severity judgment (see severity-assessment rules)."""

    suggested: str | None = None  # S1 | S2 | S3 | S4
    confidence: str | None = None  # high | medium | low
    rationale: str | None = None


class DuplicateAssessment(BaseModel):
    """Duplicate judgment (see duplicate-detection rules)."""

    duplicate_of: int | None = None  # the bug this one appears to duplicate
    confidence: str | None = None  # high | medium | low
    rationale: str | None = None


class FrontendTriageResult(HackbotAgentResult):
    bug_id: int
    # Where the bug lives, as the agent read it off Bugzilla. Reported rather than
    # derived because nothing else carries it out of the run: the inputs are just a
    # bug id. Only `notify.py` uses it, to pick the channel that owns the component.
    product: str | None = None
    component: str | None = None
    # Structured plan (best-effort, parsed from the agent's final message).
    summary: str | None = None
    root_cause: str | None = None
    proposed_fix: str | None = None
    target_files: list[str] | None = None
    confidence: str | None = None
    # Handoff fields for a downstream executor agent.
    actionable: bool | None = None  # false => out of scope / nothing to fix-plan
    regressor_node: str | None = None  # hg node of the introducing changeset, if found
    relevant_tests: list[str] | None = (
        None  # existing tests covering the area (verify anchor)
    )
    # Triage judgments (best-effort, parsed from the agent's final message).
    severity_assessment: SeverityAssessment | None = None
    # None when the hunt did not run; a populated object with `duplicate_of: null`
    # means it ran and found nothing, which is the answer we are measuring.
    duplicate_assessment: DuplicateAssessment | None = None
    # This run's verdict on whether its recorded actions may reach the bug without a
    # human, computed by `may_apply_unattended`. hackbot-api reads it and still has
    # the final say.
    auto_apply: bool = False
    # The agent's full final message, always present as a fallback.
    result: str | None = None


# How to cite a source file in the recorded comment. Injected into system.md
# rather than written there literally, because the template is run through
# str.format and the placeholder's braces would need doubling twice over in the
# prompt file; a substituted value passes through untouched.
_EXAMPLE_PATH = "browser/components/tabbrowser/content/tabgroup.js"
SEARCHFOX_LINKS_PROMPT = (
    "Every source file you reference in your Bugzilla comment must be an inline "
    f"Markdown link built from the `{SEARCHFOX_PLACEHOLDER}` placeholder, which "
    "is expanded into a revision-pinned Searchfox URL when your comment is "
    "recorded:\n\n"
    f"    [tabgroup.js]({SEARCHFOX_PLACEHOLDER}/{_EXAMPLE_PATH})\n\n"
    "- Write the placeholder **literally**. Do not put a revision, `tip` or "
    "`HEAD` in it, and do not write a searchfox.org URL yourself — you do not "
    "know which revision is being linked.\n"
    "- After it, give the repo-relative path, plus a line anchor when you know "
    "the line: `#1234`, or `#1234-1250` for a range. No `L` prefix.\n"
    "- **Use the file name alone as the link text**, not the full path: the "
    "path is already in the URL, and repeating it inline is most of what makes "
    "these comments hard to read. Keep the full repo-relative path in the URL. "
    "When two files you cite share a name, add just enough parent directory to "
    "tell them apart (`Crossword/Crossword.jsx`). No backticks around the link "
    "text — backticked text does not render as a link.\n"
    "- Leave the paths in the trailing ```json plan block as **bare paths** — "
    "that block is parsed by a downstream tool, and a link there would corrupt "
    "it.\n"
    "- Only cite a path you have confirmed exists (Read/Glob it, or take it from "
    "a Searchfox result). A path that is not in the checkout is stripped back to "
    "plain text rather than linked, so guessing costs you the link."
)


def render_scope(scope: tuple[ScopedComponent, ...] = TRIAGE_SCOPE) -> str:
    """Render `config.TRIAGE_SCOPE` as the prompt's component list, grouped by area.

    Generated rather than written into the prompt so that the component list has one
    home. The `rules/areas/` guidance stays hand-authored: it is prose about a
    codebase, and only the enumeration is mechanical.

    Takes the registry as an argument so a test can assert the grouping against a fixed
    input rather than against whatever the real scope happens to be today.
    """
    by_area: dict[str, list[str]] = {}
    for entry in scope:
        by_area.setdefault(entry.area, []).append(entry.key)

    lines = [f"- **{area}** — {', '.join(keys)}." for area, keys in by_area.items()]

    return "\n".join(
        lines
        + [
            "",
            # Two failure modes to close off, in order of how much they cost. Reading the
            # list as exhaustive gets an in-scope bug declared out of scope, which is the
            # `ecea6ca6` mistake. Reading it as a vocabulary gets a component "tidied" to
            # match, and since the component is also the routing key, `notify.py` then
            # silently tells nobody.
            "**This list is not the limit of what you triage.** It is where bugs "
            "normally come from, and which team each one reports to. `scoping.md` is "
            "what decides scope: any user-facing Firefox defect is in scope, including "
            "in a component not named above — triage it normally rather than calling it "
            "out of scope for being absent here.",
            "",
            "It is also **not** a vocabulary for the `product` and `component` fields "
            "of your plan. Copy those from Bugzilla verbatim, even when they are not "
            "listed above.",
        ]
    )


async def fetch_product_component(
    bugzilla_mcp_server: McpServerConfig, bug: int
) -> tuple[str | None, str | None]:
    """The bug's product and component, read through the Bugzilla broker.

    The agent fetches the bug itself at step 1, but the prompt is built and frozen
    before that, and the area guidance goes into it. Via the broker because the agent
    container binds no Bugzilla credentials (see `compose.yml`).

    Never raises. ``(None, None)`` makes `areas_for` send every area, which is the
    prompt this replaced -- a broken lookup must not take down a workable run.
    """
    url = (
        bugzilla_mcp_server.get("url")
        if isinstance(bugzilla_mcp_server, dict)
        else None
    )
    if not url:
        return None, None

    try:
        async with streamablehttp_client(url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(
                    "get_bugs",
                    # `id` is not optional: `get_bugs` diffs requested against
                    # returned ids to report inaccessible bugs, so leaving it out
                    # of `include_fields` makes the tool itself raise KeyError.
                    {"ids": [bug], "include_fields": "id,product,component"},
                )
                if res.isError:
                    raise RuntimeError(
                        "".join(getattr(c, "text", "") for c in res.content)
                    )
                bugs = json.loads(res.content[0].text).get("bugs") or []
                if not bugs:
                    return None, None
                return bugs[0].get("product"), bugs[0].get("component")
    except Exception as e:  # - see docstring; every failure fails open
        print(
            f"[frontend_triage] component lookup failed ({type(e).__name__}: {e}); "
            f"sending every area's guidance",
            file=sys.stderr,
        )
        return None, None


def render_area_index() -> str:
    """One line per area: its name and the trees it covers.

    Always in the prompt, even when one area's guidance is, so the agent can recognise
    that it has localized into an area it does not have.
    """
    return "\n".join(f"- **{a.name}** — {', '.join(a.trees)}" for a in AREAS)


def read_area_guidance(areas: Sequence[Area]) -> str:
    """The `rules/areas/` files for ``areas``, concatenated for the prompt.

    Headings drop two levels on the way in. The files are `# <area>` because
    `load_area_guidance` serves them whole, but that H1 pasted between
    `# Source repository` and `# Linking source files` reads as a new section.
    """
    bodies = []
    for area in areas:
        text = (AREAS_DIR / f"{area.slug}.md").read_text().strip()
        bodies.append(re.sub(r"^(#{1,4}) ", r"##\1 ", text, flags=re.MULTILINE))
    return "\n\n".join(bodies)


def load_system_prompt(rules_dir: Path, extra: str, areas: Sequence[Area]) -> str:
    tmpl = (HERE / "prompts" / "system.md").read_text()

    return tmpl.format(
        rules_dir=str(rules_dir.resolve()),
        extra_instructions=extra or "(none)",
        searchfox_links=SEARCHFOX_LINKS_PROMPT,
        triaged_components=render_scope(),
        area_index=render_area_index(),
        area_guidance=read_area_guidance(areas),
    )


def make_investigator() -> AgentDefinition:
    """Create a single generic, read-only investigator subagent definition."""
    return AgentDefinition(
        description=(
            "Focused investigator for answering a specific question about "
            "a bug or the source tree. The main agent writes your complete "
            "instructions at spawn time -- follow them precisely and return "
            "only what was asked for."
        ),
        prompt=(
            "You are a focused investigator subagent. You will be given a "
            "self-contained task by the triage agent. Complete it and return "
            "a concise answer. You have read-only access only: do not modify "
            "the source tree or Bugzilla. Do not speculate beyond what you can "
            "verify."
        ),
        tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            *BUGZILLA_READ_TOOLS,
            *SEARCHFOX_TOOLS,
            *MOZILLA_VCS_TOOLS,
        ],
        model="inherit",
    )


_DUPLICATE_HUNTER_PROMPT = """You decide one thing: is this bug already filed?

You are given a bug id, its product and component, and the discriminating signal the
triage agent pulled from it. Work only from what you can read on Bugzilla.

# Approach

1. **Search once**, scoped to the same product and component, using the best single term
   from the discriminating signal. Pass narrow fields:
   `include_fields=id,summary,status,resolution,dupe_of`. Ask for at most 20. Request
   `dupe_of` explicitly -- it is not in the default field set. Leave `description` out
   here: you need it for one candidate, not twenty.
2. **Widen at most three times** if nothing plausible comes back: drop the term, try your
   second-best signal, keep the product and component scope. Then stop.
3. **Verify only your single best candidate.** Ask for `description` in `include_fields`
   -- it is the original report, the same text as comment 0, and it comes back from
   `get_bugs` in one call. Never fetch comment threads: the rest of a thread is discussion,
   not what the bug is about, and it is many times the size. Do not verify runners-up.

# What counts as a duplicate

The same concrete defect, not the same area. Same component and a similar-sounding summary
is not a match. Ask whether fixing one would fix the other; if not, it is not a duplicate.

- The subject bug is itself `RESOLVED DUPLICATE` -> report its `dupe_of` target.
- Two candidates both match -> pick the older (lower id).
- A candidate is the subject bug itself -> that is not a duplicate. Report `NEW`.
- You could not read the bug, or the search failed -> report `NEW` and say why.

Prefer `NEW`. A wrong duplicate sends a human to the wrong bug; a missed one costs nothing
but a search.

# Output

One or two sentences saying what you found and why it does or does not match, then a final
line that is exactly one of:

```
VERDICT: <bug_id>
VERDICT: NEW
```

Nothing after that line. The triage agent reads it to decide what to put on the bug."""


def make_duplicate_hunter() -> AgentDefinition:
    """Create the duplicate-detection subagent.

    Purpose-named rather than another `investigator` spawn: that one carries 18 tool
    schemas and inherits the run's model and effort, which measured ~7x the cost for
    the same work. Keep the tool list and `effort` as they are. Read-only Bugzilla
    tools mean it cannot record anything or recurse.
    """
    return AgentDefinition(
        description=(
            "Decides whether a bug duplicates one already filed in the same "
            "component. Give it the bug id, its product and component, and the "
            "discriminating signal. Returns a VERDICT line."
        ),
        prompt=_DUPLICATE_HUNTER_PROMPT,
        tools=[
            "mcp__bugzilla__search_bugs",
            "mcp__bugzilla__get_bugs",
        ],
        model="inherit",
        effort="low",
        maxTurns=6,
    )


CONFIDENCE_LEVELS = ("high", "medium", "low")


def parse_confidence(value: object) -> str | None:
    """One of :data:`CONFIDENCE_LEVELS`, or None if ``value`` isn't one.

    The value comes out of the agent's free-form JSON block and decides whether the
    run's actions are posted, so casing and stray whitespace are tolerated rather
    than letting `"High"` read as "not high". Anything else returns None, so callers
    fail closed rather than inventing a level.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in CONFIDENCE_LEVELS else None


def parse_severity(value: object) -> str | None:
    """One of :data:`~.config.TRIAGE_SEVERITIES`, or None if ``value`` isn't one.

    Same contract as :func:`parse_confidence`, for the same reason: the level comes out
    of the agent's free-form JSON block, and it is the only severity signal reaching a
    human now that the field is no longer written.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if normalized in TRIAGE_SEVERITIES else None


def parse_bug_id(value: object) -> int | None:
    """A positive Bugzilla bug id, or None.

    Accepts the string form too -- the id comes out of the agent's free-form JSON block,
    and a model that wrote `"1998432"` means the same thing as `1998432`. `bool` is
    rejected explicitly because it is an `int` subclass in Python.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip()) or None
    return None


def parse_severity_assessment(value: object) -> SeverityAssessment | None:
    """A :class:`SeverityAssessment` with its level and confidence normalized.

    Fields degrade independently: an unreadable level or confidence becomes None, which
    drops the comment's severity block, rather than discarding the rationale with it.
    """
    if not isinstance(value, dict):
        return None
    rationale = value.get("rationale")
    return SeverityAssessment(
        suggested=parse_severity(value.get("suggested")),
        confidence=parse_confidence(value.get("confidence")),
        rationale=rationale if isinstance(rationale, str) else None,
    )


def parse_duplicate_assessment(value: object) -> DuplicateAssessment | None:
    """A :class:`DuplicateAssessment` with its bug id and confidence normalized.

    Same contract as :func:`parse_severity_assessment`, with one addition: `duplicate_of`
    is coerced to an int here rather than left to pydantic. A non-int would otherwise
    raise out of the run *after* the agent had finished, discarding the whole plan.
    """
    if not isinstance(value, dict):
        return None
    rationale = value.get("rationale")
    return DuplicateAssessment(
        duplicate_of=parse_bug_id(value.get("duplicate_of")),
        confidence=parse_confidence(value.get("confidence")),
        rationale=rationale if isinstance(rationale, str) else None,
    )


def may_apply_unattended(plan: dict) -> bool:
    """Whether this run's recorded actions may reach the bug without a human.

    Recorded as ``findings.auto_apply``. This lives in the agent rather than in
    hackbot-api because it turns on the agent's self-reported rating, which arrives
    in the final message *after* every action is already recorded.

    Two conditions, both failing closed on a plan that didn't parse:

    - ``confidence: high`` — the run localized the cause in specific code.
    - ``actionable`` is not false. ``rules/scoping.md`` pairs an out-of-scope report
      with ``confidence: low``, but nothing makes the agent pair them, so a
      ``high`` + ``actionable: false`` run would otherwise post an out-of-scope note
      on the strength of the confidence alone. The test is ``is not False`` so that
      a missing ``actionable`` doesn't read as out of scope.
    """
    return plan.get("confidence") == "high" and plan.get("actionable") is not False


def parse_plan(text: str | None) -> dict:
    """Extract the structured plan from the agent's final message, if present.

    Returns an empty dict when no parseable ```json block is found -- the raw
    text is still preserved in ``FrontendTriageResult.result``.
    """
    if not text:
        return {}
    matches = _JSON_BLOCK.findall(text)
    if not matches:
        return {}
    try:
        data = json.loads(matches[-1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    def _as_list(value):
        if isinstance(value, str):
            return [value]
        return value if isinstance(value, list) else None

    def _as_str(value):
        # A non-string would fail FrontendTriageResult's validation and lose the whole
        # run's result, and these two only route a notification.
        return value.strip() or None if isinstance(value, str) else None

    actionable = data.get("actionable")
    if not isinstance(actionable, bool):
        actionable = None
    return {
        "product": _as_str(data.get("product")),
        "component": _as_str(data.get("component")),
        "summary": data.get("summary"),
        "root_cause": data.get("root_cause"),
        "proposed_fix": data.get("proposed_fix"),
        "target_files": _as_list(data.get("target_files")),
        "confidence": parse_confidence(data.get("confidence")),
        "actionable": actionable,
        "regressor_node": data.get("regressor_node"),
        "relevant_tests": _as_list(data.get("relevant_tests")),
        "severity_assessment": parse_severity_assessment(
            data.get("severity_assessment")
        ),
        "duplicate_assessment": parse_duplicate_assessment(
            data.get("duplicate_assessment")
        ),
    }


async def run_frontend_triage(
    *,
    bugzilla_mcp_server: McpServerConfig,
    source_repo: Path,
    bug: int,
    instructions: str = "",
    task: str | None = None,
    rules_dir: Path | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    effort: str | None = None,
    verbose: bool = False,
    log: Path | None = None,
    actions_recorder: ActionsRecorder | None = None,
) -> FrontendTriageResult:
    """Triage and plan a fix for a single Bugzilla frontend bug (read-only).

    Returns a :class:`FrontendTriageResult` on success; raises
    :class:`AgentError` if the agent ends in an error.
    """
    if rules_dir is None:
        rules_dir = HERE / "rules"

    print(f"[frontend_triage] triaging bug {bug}", file=sys.stderr)

    # Action-recording MCP server (in-process). Standalone/script runs pass
    # actions_recorder=None and get a local recorder (no uploader).
    actions_recorder, actions_server = actions_server_for(
        actions_recorder, types=ENABLED_ACTION_TYPES
    )
    enabled_action_tools = actions_to_tool_names(ENABLED_ACTION_TYPES)

    # In-process MCP servers for read-only code investigation. Searchfox and HGMO
    # are public (no credentials), so they run in-process rather than via a
    # brokered sidecar.
    searchfox_server = build_sdk_server(
        "searchfox", SearchfoxContext(client=AsyncSearchfoxClient()), searchfox.TOOLS
    )
    vcs_server = build_sdk_server("mozilla_vcs", MozillaVcsContext(), mozilla_vcs.TOOLS)

    # Pin every source-file reference in the recorded comment to one revision.
    # The agent writes a placeholder (see SEARCHFOX_LINKS_PROMPT) that this hook
    # expands as the comment is recorded, so the agent never handles the SHA. An
    # unresolvable revision degrades to revision-agnostic /source/ links.
    searchfox_rev = await resolve_index_revision()
    if not searchfox_rev:
        print(
            "[frontend_triage] no searchfox revision; linking tip-of-tree",
            file=sys.stderr,
        )
    # Bound what the agent may record, at the moment it records it. These are the
    # only check on what an unattended run writes to a bug — see hooks.py. Registered
    # ahead of the hooks below so a refusal happens before the comment body is
    # rewritten.
    actions_recorder.add_hook(
        "bugzilla.add_comment", add_comment_hook(actions_recorder, bug)
    )
    actions_recorder.add_hook("bugzilla.add_comment", severity_block_hook)

    # Which areas' guidance goes in the prompt. Falls back to every area when the bug's
    # component is unknown or the lookup failed, which is what the prompt carried before
    # this was split up -- see `areas_for`.
    product, component = await fetch_product_component(bugzilla_mcp_server, bug)
    areas = areas_for(product, component)
    loaded_areas = {area.name for area in areas}
    print(
        f"[frontend_triage] {product} :: {component} -> "
        f"{', '.join(sorted(loaded_areas))}",
        file=sys.stderr,
    )

    # Registered before `permalink_hook`, which rewrites the placeholders this reads.
    actions_recorder.add_hook("bugzilla.add_comment", area_guidance_hook(loaded_areas))

    actions_recorder.add_hook(
        "bugzilla.add_comment",
        permalink_hook(permalink_prefix(searchfox_rev), source_repo.resolve()),
    )
    actions_recorder.add_hook("bugzilla.add_comment", feedback_tags_hook)

    # Shares `loaded_areas` with the hook above, so an area the agent pulls mid-run
    # stops the hook refusing a comment that cites it.
    areas_server = build_sdk_server(
        "areas",
        AreaGuidanceContext(areas_dir=AREAS_DIR, loaded=loaded_areas),
        area_tools.TOOLS,
    )

    system_prompt = load_system_prompt(rules_dir, instructions, areas)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        mcp_servers={
            "bugzilla": bugzilla_mcp_server,
            "searchfox": searchfox_server,
            "mozilla_vcs": vcs_server,
            "areas": areas_server,
            ACTIONS_SERVER_NAME: actions_server,
        },
        agents={
            "investigator": make_investigator(),
            "duplicate_hunter": make_duplicate_hunter(),
        },
        cwd=str(source_repo.resolve()),
        add_dirs=[str(rules_dir.resolve())],
        permission_mode="bypassPermissions",
        # Read-only investigation tools only: no Write/Edit (source is never
        # modified) and no firefox build/eval tools.
        allowed_tools=[
            "Read",
            "Grep",
            "Glob",
            "Bash",
            "Task",
            *BUGZILLA_READ_TOOLS,
            *SEARCHFOX_TOOLS,
            *MOZILLA_VCS_TOOLS,
            *AREA_TOOLS,
            *enabled_action_tools,
        ],
        model=model,
        max_turns=max_turns,
        **({"effort": effort} if effort else {}),
        setting_sources=[],
    )

    rules_path = rules_dir.resolve()
    if task:
        user_prompt = (
            f"Bug to work on: {bug}\n\n"
            f"Task: {task}\n\n"
            f"The rules in {rules_path} are available if the task "
            f"calls for them, but the task above is your primary "
            f"directive -- it overrides the default triage workflow."
        )
    else:
        user_prompt = (
            f"Triage bug {bug}.\n\nConsult the relevant rules in {rules_path}."
        )

    result_msg: ResultMessage | None = None
    with Reporter(verbose=verbose, log_path=log) as reporter:
        reporter.header(f"bug {bug}")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(user_prompt)
            async for msg in client.receive_response():
                reporter.message(msg)
                if isinstance(msg, ResultMessage):
                    result_msg = msg

    if result_msg is None:
        raise AgentError(f"bug {bug}: agent produced no result message")
    if result_msg.is_error:
        raise AgentError(
            f"bug {bug} triage failed: {result_msg.result or result_msg.subtype}"
        )

    plan = parse_plan(result_msg.result)

    return FrontendTriageResult(
        bug_id=bug,
        result=result_msg.result,
        num_turns=result_msg.num_turns,
        total_cost_usd=result_msg.total_cost_usd,
        auto_apply=may_apply_unattended(plan),
        **plan,
    )
