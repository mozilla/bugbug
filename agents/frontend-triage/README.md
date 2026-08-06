# frontend-triage agent

Triages a Firefox desktop frontend bug from Bugzilla and produces a **root-cause
analysis plus a proposed fix plan**. It reads the source tree, navigates the
codebase with Searchfox, and inspects regressor changesets on hg.mozilla.org. It
does **not** build Firefox, edit source, reproduce the bug, or write to Bugzilla.

It deliberately stops at a plan: visual and interaction bugs can't be verified by
the crash-reproduction loop the [`bug-fix`](../bug-fix/) agent relies on, so a
human (or a downstream execution agent) takes it from there.

## What it triages

Firefox desktop **frontend defects** — the kind documented with a screenshot or
steps to reproduce rather than a stack trace: Tabbed Browser (incl. Split View
and Tab Groups), New Tab Page, Address Bar, Menus, Toolbars and Customization,
Sidebar, Theme.

Poor fits: crashes, hangs, assertions and sanitizer reports (those belong to
[`bug-fix`](../bug-fix/)), anything with no frontend component, and bugs whose
fix can only be judged by _seeing_ the rendered result.

The `scoping.md` ruleset runs first and filters out non-defects, tracking/`meta`
bugs and intermittent test failures with a short note instead of an invented fix
plan.

## Running it locally

Needs Docker running, an Anthropic API key with billing enabled, and a Bugzilla
API key (reads only — one from an account without edit rights works fine). Put
the secrets in a gitignored `.env` at the repo root:

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
they come from `.env`, `compose.yml`, or the command line.

| Env var             | Required | Meaning                                                                                                        |
| ------------------- | -------- | -------------------------------------------------------------------------------------------------------------- |
| `BUG_ID`            | yes      | The Bugzilla bug to triage                                                                                     |
| `BROKER_URL`        | yes      | Bugzilla broker base URL; the agent appends `/mcp`. `compose.yml` sets it                                      |
| `ANTHROPIC_API_KEY` | yes      | Drives the agent (billed per token)                                                                            |
| `BUGZILLA_API_URL`  | yes      | e.g. `https://bugzilla.mozilla.org` — **broker container only**                                                |
| `BUGZILLA_API_KEY`  | yes      | **Broker container only**; reads only. The agent never sees it                                                 |
| `MODEL`             | no       | Defaults to `claude-opus-5` (`DEFAULT_MODEL` in `__main__.py`); pinned so runs are reproducible and comparable |
| `MAX_TURNS`         | no       | Hard cap on loop iterations — a runaway guard, cut off if hit                                                  |
| `EFFORT`            | no       | `low` \| `medium` \| `high` \| `xhigh` \| `max`; only passed when set                                          |

## Output

Each run writes to `~/hackbot/artifacts/<run_id>/`:

- **`summary.json`** — `findings` holds the structured plan (`root_cause`,
  `proposed_fix`, `target_files`, `confidence`) plus the executor handoff fields
  `actionable`, `regressor_node` and `relevant_tests`, plus `auto_apply` — the
  run's own verdict on whether it may be posted without review. `actions` holds
  the **recorded** Bugzilla comment (and, at high confidence, possibly a field
  change). Recording is not posting, but see below: an `auto_apply` run's actions
  do reach the bug unattended.
- **`logs/agent.log`** — the streamed reasoning and every tool call, and the only
  record of which model actually ran.
- **No `changes/` directory.** Its absence confirms the run stayed read-only.

Two caveats before acting on a plan:

- **`confidence` describes the diagnosis, not the fix.** It reflects how clearly
  the agent pinned a root cause in the code, never whether the fix works — it
  cannot run anything. Read `high` as "trust the diagnosis, still review the
  patch."
- **Line numbers are model-asserted.** The permalink pins the revision, so a
  cited line can't drift out from under the link, but the number is still the
  model's claim. Trust the files, functions and selectors it names; confirm exact
  lines against the source.

## What it writes back to Bugzilla

**Nothing, during a run.** `ENABLED_ACTION_TYPES` in `config.py` allows
`bugzilla.add_comment` and `bugzilla.update_bug`, but those come from an
in-process actions server that appends to `summary.json` and makes no network
calls. The only Bugzilla access the agent has is through the broker sidecar,
which exposes five read tools and holds the API key.

**Afterwards, though, a confident run posts itself.** When the run reports
`confidence: high` and does not report `actionable: false`, it sets
`findings.auto_apply`, and hackbot-api applies the recorded actions to the real
bug with nobody in between. `may_apply_unattended()` in `agent.py` is that
decision in full. Medium and low are held for a person to apply from the Hackbot
UI, as before.

Judgement and reach are bounded separately, because an action's params are model
output no matter how sure the agent is — and the agent spends the run reading bug
comments nobody controls. Two hooks in `hooks.py` refuse an action outright as it
is recorded, and they are the only thing bounding what an unattended run writes:
hackbot-api applies whatever it finds in `summary.json`, dispatching it against a
handler registry far wider than the tools this agent was given.

- `add_comment_hook` — one comment, public, on the bug being triaged.
- `update_bug_hook` — one field change, add-only, on the bug being triaged, and
  only `keywords`/`severity` with values from `TRIAGE_SEVERITIES` /
  `TRIAGE_KEYWORDS` in `config.py`. Widen those sets alongside the rule that needs
  the new value.

A refusal reaches the agent as a tool error it can correct in the same run, and the
action never lands in `summary.json`. The action _type_ needs no check:
`ENABLED_ACTION_TYPES` decides which tools the actions server exposes at all.

Two further hooks shape the comment text as it is recorded:

- `permalink_hook` expands `{{searchfox.permalink}}/<path>#<line>` into
  `https://searchfox.org/firefox-main/rev/<sha>/…`, so every source reference
  keeps pointing at the code the agent actually read. A path that isn't in the
  checkout is unwrapped to backticked plain text rather than linked, because a
  permalink to a hallucinated path 404s for whoever clicks it. See the module
  docstring in `libs/hackbot-runtime/hackbot_runtime/searchfox.py` for the full
  scheme and why the revision comes from Searchfox's index rather than the
  checkout.
- `feedback_tags_hook` (`agent.py`) appends the triage-specific tags a reader can
  add to categorize a problem: `ai-triage-wrong-file`, `ai-triage-wrong-cause`,
  `ai-triage-hallucination`, `ai-triage-out-of-scope`.

Below both sits the runtime's shared footer inviting a 👍 or 👎 reaction. Those
reactions and tags are the feedback channel — the agent does not request needinfo.

## Tuning

`rules/` and `prompts/` both live under `hackbot_agents/frontend_triage/`.

- **`rules/`** is the main behavior dial. `scoping.md` decides what gets skipped;
  `frontend-triage.md` sets in-scope components, comment content, and the
  confidence thresholds for recording an action. The agent globs the directory
  and reads only what it judges relevant, so new `.md` files extend it — see
  `rules/README.md` for how to author one.
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

There are no unit tests for this agent; CI covers the shared machinery it builds
on via the `libs/agent-tools` and `libs/hackbot-runtime` suites.
