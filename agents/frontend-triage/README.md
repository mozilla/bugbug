# frontend-triage agent

Triages a Firefox desktop frontend bug from Bugzilla and produces a **root-cause
analysis plus a proposed fix plan**. It reads the source tree, navigates the
codebase with Searchfox, and inspects regressor changesets on hg.mozilla.org. It
does **not** build Firefox, edit source, reproduce the bug, or write to Bugzilla.

Think of it as first-pass triage on a UI papercut: read the bug, find the
responsible code, explain the likely cause, propose a fix — then hand that to a
human or an execution agent to implement and verify. It deliberately stops at a
plan, because visual and interaction bugs can't be verified by the
crash-reproduction loop the [`bug-fix`](../bug-fix/) agent relies on.

## What it's for

Firefox desktop **frontend defects** — the kind documented with a screenshot or
steps to reproduce rather than a stack trace. Tabbed Browser (incl. Split View
and Tab Groups), New Tab Page, Address Bar, Menus, Toolbars and Customization,
Sidebar, Theme.

Poor fits: crashes, hangs, assertions and sanitizer reports (those belong to
[`bug-fix`](../bug-fix/)); anything with no frontend component; and bugs whose
fix can only be judged by _seeing_ the rendered result — it can localize and
propose, but never visually confirm.

Out-of-scope bugs are filtered automatically. `rules/scoping.md` runs first and
has the agent skip non-defects, tracking/`meta` bugs, and intermittent test
failures with a short note instead of an invented fix plan.

## Safety: it cannot write to Bugzilla or to the source tree

This is structural, not just prompting:

- **No Bugzilla write tool exists.** The agent reaches Bugzilla only through the
  broker sidecar, which exposes five read tools and nothing else. The API key
  lives in the broker; the agent process never sees it.
- **"Actions" only record to disk.** `bugzilla_add_comment` /
  `bugzilla_update_bug` come from a separate in-process server that appends to a
  list serialized into `summary.json`. They make no network calls. Applying them
  is a separate downstream step, not part of a run.
- **No write or build tools are granted.** `agent.py` builds `allowed_tools` from
  read-only inspection tools plus the Bugzilla, Searchfox and Mozilla-VCS read
  tools. There is no `Write`/`Edit` and no Firefox build/eval tool. Searchfox and
  hg.mozilla.org are queried over HTTP for public data only.

## Running it locally

Needs Docker running, an Anthropic API key with billing enabled, and a Bugzilla
API key (reads only — one from an account without edit rights works fine).

Put the secrets in a gitignored `.env` at the repo root:

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
BUGZILLA_API_URL=https://bugzilla.mozilla.org
BUGZILLA_API_KEY=...
```

Then run from the repo root — the root `docker-compose.yml` includes this
agent's `compose.yml`, so the service and its broker sidecar are available:

```sh
BUG_ID=2014702 docker compose up frontend-triage-agent --build
```

The first run shallow-clones mozilla-central into a Docker volume: expect several
minutes and a large download. Later runs reuse the volume.

Three bugs that exercise the classes this agent handles:

| Bug       | Class       | Notes                                           |
| --------- | ----------- | ----------------------------------------------- |
| `2014702` | Behavioral  | New Tab weather widget vanishing                |
| `2014629` | Pure visual | Split View group-line CSS gap                   |
| `2004297` | Regression  | Print Preview shift; traces the named regressor |

## Inputs

Environment variables. `hackbot-api` derives them from the input schema; locally
they come from `.env` or the command line.

| Env var             | Required | Meaning                                                        |
| ------------------- | -------- | -------------------------------------------------------------- |
| `BUG_ID`            | yes      | The Bugzilla bug to triage                                     |
| `ANTHROPIC_API_KEY` | yes      | Drives the agent (billed per token)                            |
| `BUGZILLA_API_URL`  | yes      | e.g. `https://bugzilla.mozilla.org`                            |
| `BUGZILLA_API_KEY`  | yes      | Held by the broker; reads only                                 |
| `MODEL`             | no       | Defaults to `claude-opus-5` (`DEFAULT_MODEL` in `__main__.py`) |
| `MAX_TURNS`         | no       | Hard cap on loop iterations — a runaway guard, cut off if hit  |
| `EFFORT`            | no       | `low` \| `medium` \| `high` (default) \| `xhigh` \| `max`      |

The model is pinned rather than left to the Claude Code CLI's default, so runs
are reproducible and identical locally and in the cloud. An explicit value wins,
which is what makes model comparisons possible.

## Reading the output

Each run writes to `~/hackbot/artifacts/<run_id>/`:

- **`summary.json`** — `findings` holds the structured plan (`root_cause`,
  `proposed_fix`, `target_files`, `confidence`) plus the executor handoff fields
  `actionable`, `regressor_node` and `relevant_tests`. `actions` holds the single
  **recorded** `bugzilla.add_comment` — written here for review, not posted.
- **`logs/agent.log`** — the streamed reasoning and every tool call, and the only
  record of which model actually ran.
- **No `changes/` directory.** Its absence confirms the run stayed read-only.

Two things to know before acting on a plan:

- **`confidence` describes the diagnosis, not the fix.** It reflects how clearly
  the agent pinned a root cause in the code, never whether the fix works — it
  cannot run anything. Read `high` as "trust the diagnosis, still review the
  patch."
- **Line numbers are model-asserted and drift.** Trust the files, functions and
  selectors it names; confirm exact lines against the source.

## Tuning

- **`rules/`** is the main behavior dial. `scoping.md` decides what gets skipped;
  `frontend-triage.md` sets in-scope components, comment content, and the
  confidence thresholds for recording an action. The agent globs the directory
  and reads only what it judges relevant, so new `.md` files extend it.
- **`prompts/system.md`** holds the standing instructions: output format, the
  read-only mandate, and when to reach for Searchfox versus reading a file.
- **Cost** scales with tool use, not just turns — Searchfox results are
  token-heavy, so narrowing queries (`path_filter`, a modest `limit`) matters
  more than `MAX_TURNS` when batching.

## Registration

Registered with `hackbot-api` as `FrontendTriageInputs` in
`services/hackbot-api/app/schemas.py` and a `frontend-triage` entry in
`app/agents.py` (job `hackbot-agent-frontend-triage`). Local Compose runs don't
need the API.
