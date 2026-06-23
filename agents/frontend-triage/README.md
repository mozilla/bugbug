# frontend-triage agent

A Hackbot agent that **triages Firefox desktop frontend bugs** from Bugzilla and
produces a **root-cause analysis + a proposed fix plan**. It investigates the
Firefox source tree read-only and writes its findings to disk; it does **not**
build Firefox, modify source, reproduce the bug, or write anything to Bugzilla.

Think of it as an experienced engineer doing first-pass triage on a UI/UX
papercut: it reads the bug, finds the responsible code, explains the likely
cause, and proposes how to fix it — then hands that off to a human (or a
downstream execution agent) to actually implement and verify.

It is a sibling of the reference [`bug-fix`](../bug-fix/) agent, which targets
*crash/sanitizer* bugs and goes all the way to a verified patch. `frontend-triage`
deliberately stops at a plan, because visual/interaction bugs can't be verified
by the crash-reproduction loop `bug-fix` relies on.

---

## What it's for

Good fits — Firefox desktop **frontend** defects, typically documented with a
video/screenshot and steps to reproduce, not a crash:

- `Firefox :: Tabbed Browser` / `Tabbed Browser: Split View`
- `Firefox :: New Tab Page`
- `Firefox :: Address Bar`, `Menus`, `Toolbars and Customization`, `Sidebar`, `Theme`

Poor fits (use a different agent / manual triage):

- Crashes, hangs, assertions, sanitizer reports → these belong to [`bug-fix`](../bug-fix/).
- Backend/platform bugs with no frontend component.
- Bugs whose fix can only be judged by *seeing* the rendered result — the agent
  can localize and propose, but cannot visually confirm.

---

## What it produces

For each run, in `~/hackbot/artifacts/<run_id>/`:

- **`summary.json`** — the machine-readable result:
  - `findings` → the structured plan: `summary`, `root_cause`, `proposed_fix`,
    `target_files`, `confidence` (`high|medium|low`), plus `num_turns` and
    `total_cost_usd`.
  - `actions` → a single **recorded** `bugzilla.add_comment` (the human-readable
    triage comment). "Recorded" means written to this file for review — **it is
    not posted to Bugzilla.**
- **Logs** of the agent's streamed reasoning.

It never produces a `changes/` directory (that would mean source edits) — its
absence is your confirmation the run stayed read-only.

---

## Safety guarantees (and why they hold)

Nothing this agent does can write to Bugzilla or modify the source tree. This is
enforced structurally, not just by prompt instructions:

1. **No Bugzilla write tool exists.** The agent reaches Bugzilla only through the
   broker sidecar, which exposes exactly five **read** tools (`search_bugs`,
   `get_bugs`, `get_bug_comments`, `get_bug_attachments`, `download_attachment`
   — see `agent_tools/bugzilla.py`). There is no update/comment/create tool in
   the Bugzilla toolset at all. The Bugzilla API key lives only in the broker and
   is used solely for reads.
2. **"Actions" only record to disk.** The `bugzilla_add_comment` / `bugzilla_update_bug`
   tools come from a separate in-process server and merely append to a list that
   is serialized into `summary.json` (`ActionsRecorder.record`). They make no
   network calls. Applying them to Bugzilla is a separate downstream step that is
   **not** part of local runs.
3. **No write/build tools are granted.** `agent.py` builds `allowed_tools` from
   read-only inspection tools (`Read`, `Grep`, `Glob`, `Bash`, `Task`) + the
   Bugzilla read tools + the record-only action tools. There are no `Write`/`Edit`
   tools and no Firefox build/eval tools.

`config.py` further restricts recordable actions to `bugzilla.add_comment` and
`bugzilla.update_bug` (no attachments, no bug creation), and the system prompt
forbids private comments and `RESOLVED` status changes.

---

## How it works

```
                         ┌─────────────────────────────┐
   Bugzilla (read-only)  │  frontend-triage-broker      │
   bugzilla.mozilla.org ─┤  (sidecar; holds API key)    │
                         │  exposes 5 read tools (MCP)  │
                         └──────────────┬──────────────┘
                                        │ MCP over HTTP (read-only)
                                        ▼
   git clone (shallow)   ┌─────────────────────────────┐
   mozilla-firefox/      │  frontend-triage-agent       │
   firefox  ───────────► │                              │
   (workspace volume)    │  Claude Agent SDK loop:      │
                         │   - read bug + comments      │
                         │   - read relevant rules/     │
                         │   - investigate source       │
                         │     (Read/Grep/Glob/Bash,    │
                         │      + investigator subagent)│
                         │   - record comment + plan    │
                         └──────────────┬──────────────┘
                                        │ records (no network write)
                                        ▼
                            ~/hackbot/artifacts/<run_id>/summary.json
```

**The run, step by step:**

1. **Startup (runtime).** `hackbot-runtime` reads `hackbot.toml`, shallow-clones
   the Firefox repo into the `workspace` volume (slow on first run, cached after),
   and builds a `HackbotContext`. There is **no** `[firefox]` table, so no build
   toolchain is prepared.
2. **Entrypoint.** `__main__.py` reads the per-run inputs (env vars), sets a
   read-only triage `task`, and calls `run_frontend_triage(...)`.
3. **Agent loop.** `agent.py` wires up the Claude Agent SDK with the read-only
   tools, the rules directory, and the action-recording server, then drives the
   loop: fetch the bug → load the relevant ruleset(s) from `rules/` → investigate
   the source (delegating deep searches to a read-only `investigator` subagent) →
   record one comment with the fix plan.
4. **Structured output.** The agent ends its final message with a fenced ```json
   block. `agent.py` parses that into the typed `FrontendTriageResult`
   (`root_cause`, `proposed_fix`, `target_files`, `confidence`), which the runtime
   writes to `summary.json` under `findings`. If the block is missing/unparseable,
   the structured fields are left null and the raw text is preserved in `result`.

**Confidence semantics:** confidence reflects how clearly the agent could pin a
**root cause** in the code, not whether the fix is verified (it never runs the
result). Treat `high` as "trust the diagnosis, still review/verify the patch."

### File layout

```
agents/frontend-triage/
  pyproject.toml          # distribution "hackbot-agent-frontend-triage"
  hackbot.toml            # [source] = mozilla-firefox/firefox; NO [firefox] table
  Dockerfile              # builds the agent + broker images
  compose.yml             # frontend-triage-{broker,agent} services for local runs
  hackbot_agents/
    frontend_triage/
      __main__.py         # inputs + read-only triage task + entrypoint
      agent.py            # the agent loop, FrontendTriageResult, plan parser
      config.py           # tool/action allow-lists (read-only; no firefox)
      broker.py           # Bugzilla read-only MCP broker (sidecar)
      prompts/system.md   # system prompt: triage + fix-plan, no build/repro
      rules/
        README.md         # how to author rulesets
        frontend-triage.md  # the frontend-papercut ruleset
```

### Configuration (per-run inputs)

Set as environment variables (the API derives these automatically from the input
schema; locally you pass them via `.env` / the command line):

| Env var | Required | Meaning |
|---|---|---|
| `BUG_ID` | yes | The Bugzilla bug to triage |
| `ANTHROPIC_API_KEY` | yes | Drives the Claude agent (billed per token) |
| `BUGZILLA_API_URL` | yes | Bugzilla instance, e.g. `https://bugzilla.mozilla.org` |
| `BUGZILLA_API_KEY` | yes | Held by the broker; used for **reads only** |
| `MODEL` | no | Override the agent model (cost/quality dial) |
| `MAX_TURNS` | no | Hard cap on agent loop iterations (runaway/cost guard; cut off if exceeded) |
| `EFFORT` | no | Reasoning effort level |

A **turn** is one iteration of the agent loop (model thinks → calls tools →
observes results). It is not a number of fix attempts; more turns just means more
investigation. Turns roughly track cost. `MAX_TURNS` cuts the loop off if hit.

---

## How to test it locally

You run it with Docker Compose from the **repo root**. No cloud, no uploader —
everything stays on your machine.

**Prerequisites**

- Docker Desktop running.
- An Anthropic API key with API billing enabled.
- A Bugzilla API key (used only for reads; you can use one from an account
  without edit rights for extra safety).

**1. Create the repo-root `.env`** (gitignored — never committed):

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
BUGZILLA_API_URL=https://bugzilla.mozilla.org
BUGZILLA_API_KEY=...
```

**2. Run against a bug** (from `/path/to/bugbug`, the repo root):

```sh
BUG_ID=2014702 docker compose up frontend-triage-agent --build
```

The root `docker-compose.yml` includes this agent's `compose.yml`, so the
`frontend-triage-agent` service (and its `frontend-triage-broker` sidecar) are
available. The first run shallow-clones the Firefox repo into a Docker volume —
expect several minutes and a large download. Subsequent runs reuse the volume and
start quickly.

**3. Read the result:**

```sh
LATEST=$(ls -t ~/hackbot/artifacts | head -1)
cat ~/hackbot/artifacts/$LATEST/summary.json
```

Check:
- `findings` → the structured plan (`root_cause`, `proposed_fix`, `target_files`,
  `confidence`).
- `actions` → exactly one `bugzilla.add_comment`, recorded (not posted). It carries
  an `*This is an automated analysis result...*` footer.
- **No `changes/` directory** and no build step — confirms the run stayed read-only.

**Good bugs to test** (validated across the three classes this agent handles):

| Bug | Class | Notes |
|---|---|---|
| `2014702` | Behavioral | New Tab weather widget vanishing |
| `2014629` | Pure visual | Split View group-line CSS gap |
| `2004297` | Regression | Print Preview shift; traces the named regressor |

**A note on line numbers:** the agent cites approximate line numbers
(e.g. `~L1234`). Those are model-asserted and can drift — trust the files and
functions/selectors it names, and confirm exact lines against the source before
acting.

---

## Tuning

- **Rules** (`rules/frontend-triage.md`): the main behavior dial. Adjust which
  components are in scope, what the comment should contain, comment brevity, and
  the confidence thresholds for taking actions. The agent Globs `rules/` and reads
  only the rulesets it judges relevant, so you can add more `.md` files for other
  scopes.
- **System prompt** (`prompts/system.md`): the standing instructions — output
  format, the read-only mandate, the structured-JSON requirement.
- **Cost ceiling**: set `MAX_TURNS` and/or a cheaper `MODEL` per run to bound cost
  when batching.

## Handoff to implementation

The structured plan in `summary.json` `findings` is designed to be consumed
downstream. To turn a plan into a candidate patch, feed it (e.g. via the `task` /
extra-instructions override) into an agent that has source-write tools — either
the existing [`bug-fix`](../bug-fix/) agent or a dedicated execution agent — which
emits a `git am`-applyable patch. A human reviews and visually verifies the diff,
since frontend fixes can't be auto-verified.

## Registration

This agent is registered with `hackbot-api` for orchestrated runs:
`FrontendTriageInputs` in `services/hackbot-api/app/schemas.py` and the
`frontend-triage` entry in `services/hackbot-api/app/agents.py` (job
`hackbot-agent-frontend-triage`). Local Compose runs do not require the API.
