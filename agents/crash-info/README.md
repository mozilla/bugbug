# crash-info

CrashInfo reads a Firefox crash bug and works out what the report is missing for
someone to understand and fix the crash: which cases the fix must cover, how long
an object stays valid, what the program was doing, the steps that lead to the
crash, and so on. It recovers what Bugzilla, crash-stats, the source and its history
establish, and infers the rest, marking each answer as **established** (with the
evidence it rests on) or **inferred** (with its reasoning and a confidence). It
does not write the fix and changes nothing in Bugzilla: its output is a report,
and a few notes that a chained `bug-fix` run can read.

It runs on the Claude Agent SDK, like the other Hackbot agents.

## How it works

The *Taxonomy of crash missing information* (`taxonomy.toml`: 13 labels in five
categories, Repair, Object validity, Execution context, Verification and Trigger)
drives every step:

```
read the bug as of the cutoff, pin the code   no model call
complete the crash data the report lacks      no model call (crash-stats)
recall earlier crash bugs fixed before it     no model call
   ▼
triage ─────────── every label: missing (a gap, as a question about this
   ▼               crash), already in the report, or not applicable
investigate ⇄ ──── lookups, each serving one gap; an answer recorded per gap
   ▼
finish ─────────── accepted once every gap has an answer
```

The model works through one in-process MCP server, `crash_info`, whose tools are
CrashInfo's own: `triage`, the lookups (`read_code`, `search_code`,
`file_history`, `read_changeset`, `crash_stats`, `crash_report`, `search_bugs`,
`read_bug`, `prior_bugs`), `add_gap`, `record_finding`, `read_evidence` and
`finish`. **No built-in tool** (Read, Grep, Bash, ...) is offered, on purpose:
every lookup goes through CrashInfo's sources, so that

- it sees only what existed at the cutoff in historical runs (later comments,
  changesets and crash reports are withheld);
- it counts against the lookup budget (`MAX_TOOL_CALLS`, 30 by default);
- its result is stored as evidence with an id, and an answer recorded as
  established must cite evidence that supports it, or it is kept as an inference.

The steps are enforced outside the model: the other tools are refused until the
triage of every label is accepted; `finish` is refused while a gap has no answer;
once the lookups or the turns are spent, the agent is asked to record its answers
and finish; a reply without a tool call gets a reminder, twice at most.

Code: `agent.py` (the SDK loop and the MCP server), `investigation.py` (reading
the report, the prompts, the steps), `react_tools.py` (the tools and their
checks), `recovery.py` and `sources/` (the lookups, bound to the cutoff),
`knowledge.py` (earlier fixed bugs), `report.py` (the report and the notes).

## Inputs

| Variable | Default | |
|---|---|---|
| `BUG_ID` | required | The crash bug. |
| `BEFORE_FIRST_PATCH` | `false` | Historical run: the bug as it was one second before its first patch. |
| `AS_OF` | unset | Historical run: the bug as it was at this time (ISO 8601). |
| `MODEL` | `claude-opus-5-5` | |
| `EFFORT` | `high` | |
| `MAX_TOOL_CALLS` | `30` | Lookups per investigation. |
| `USE_SOURCE_CHECKOUT` | `false` | Live runs: search the checkout Hackbot prepares instead of Searchfox. |

Without `AS_OF` or `BEFORE_FIRST_PATCH`, the run reads the bug as it is now.

## Output

`summary.json` holds the result (`CrashInfoResult`): the assessment, why the run
stopped, a summary, and each piece of missing information with its status and
evidence. The artifacts:

- `crash_info/report.md`: the report for people, one section per piece of
  missing information;
- `crash_info/handoff.md`: all the missing information CrashInfo found, established
  or inferred at any confidence, and what stays unknown, without the report's
  investigation log: what a chained `bug-fix` run reads;
- `crash_info/report.json` and `crash_info/state.json`: the same, structured,
  with every lookup and evidence item.

## Running it locally

With `ANTHROPIC_API_KEY` in the repository's `.env`:

```sh
BUG_ID=2044222 BEFORE_FIRST_PATCH=true docker compose up crash-info-agent --build
```

The run's summary, log and artifacts land in `~/hackbot/artifacts/<run_id>`.

Tests (offline: a scripted Claude, over MCP in memory, and a fake Mozilla):

```sh
uv run --package hackbot-agent-crash-info python -m pytest agents/crash-info/tests
```

## Limits

- It reads Bugzilla anonymously: the agent holds no Bugzilla token, so it cannot
  read restricted (security) bugs. Reading them would mean going through a broker,
  as `bug-fix` does.
- Historical runs read the code at the last push before the cutoff through
  Searchfox and hg.mozilla.org; the prepared checkout is at the current tip, so
  it is used for live runs only.
