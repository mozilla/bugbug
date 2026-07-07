# Hackbot Agents

Each subdirectory is a single, self-contained agent — its logic, its entrypoint,
and its deployment all live together, so you can understand one agent without
hunting around the repo.

New here? The best way to start is to read through **`bug-fix/`** — it's our reference
agent, and the fastest path to your own is to copy it and adapt.

## How an agent works (the important part)

When the platform runs your agent, it calls `python -m hackbot_agents.<name>`, which runs
your `__main__.py`. Your job is to fill in three small pieces:

```python
class AgentInputs(BaseSettings):          # per-run inputs, read from env (bug_id -> BUG_ID)
    bug_id: int

async def main(ctx: HackbotContext) -> BugFixResult:
    inputs = AgentInputs()
    return await run_bug_fix(bug=inputs.bug_id, ...)   # your real logic lives in agent.py

run_async(main)                           # finds hackbot.toml, runs main, exits the process
```

Three rules are worth remembering:

- **To report success,** return a `HackbotAgentResult` (subclass it with your own fields).
  The runtime saves it to `summary.json` under `findings`.
- **To report failure,** just raise. Use `AgentError("…")` when it's an expected, explainable
  failure; let any other exception bubble up for an unexpected crash.
- **`ctx` is your window to the platform** — everything it prepared for you hangs off it:
  `ctx.source_repo`, `ctx.firefox`, `ctx.anthropic.api_key`, `ctx.actions`,
  `ctx.publish_file`, `ctx.publish_json`. You never wire these up yourself.

## What's in an agent folder (`agents/<name>/`)

```
agents/<name>/
  pyproject.toml          # the distribution "hackbot-agent-<name>" and its dependencies
  hackbot.toml            # what you need the platform to prepare: [source], [firefox]
  Dockerfile              # how it ships
  compose.yml             # how to run it locally
  hackbot_agents/         # a shared namespace package — please don't add __init__.py here!
    <name_snake>/         # your agent's package (e.g. bug_fix)
      __init__.py         # empty
      __main__.py         # AgentInputs + main(ctx) + run_async(main)
      agent.py            # entrypoint: your prompts, logic, and HackbotAgentResult subclass
```

One thing to watch: **never create `hackbot_agents/__init__.py`.** Leaving it out is what
lets several agents live side by side in one environment without overwriting each other (PEP 420).
It's an easy mistake to make, and a confusing one to debug.

## Running an agent locally

Each agent ships a `compose.yml` so you can run it on your machine the same way it
ships — no platform, no uploader. The reference `bug-fix` agent is already wired into
the repo's root `docker-compose.yml`, so running it is three steps:

1. **Create a `.env` file in the repo root** with the secrets the agent needs:

   ```dotenv
   # .env (repo root) — never commit this file
   ANTHROPIC_API_KEY=sk-ant-...
   BUGZILLA_API_URL=https://bugzilla.mozilla.org
   BUGZILLA_API_KEY=...
   ```

   `docker compose` reads `.env` from the directory you run it in automatically, so
   these values flow into the services defined in the agent's `compose.yml`.

2. **Make sure the agent's `compose.yml` is included** from the root `docker-compose.yml`.
   For `bug-fix` this is already in place:

   ```yaml
   # docker-compose.yml
   include:
     - path: agents/bug-fix/compose.yml
   ```

   When you add a new agent, add its `compose.yml` to this `include` list the same way.

3. **Run it**, passing the per-run inputs inline. `BUG_ID` is the only input the
   `bug-fix` agent requires:

   ```sh
   BUG_ID=1234567 docker compose up bug-fix-agent --build
   ```

   Here `bug-fix-agent` is the service name defined in the agent's `compose.yml` —
   use that agent's own service name when you run a different one. Compose builds and
   runs the agent against that bug. The run's `summary.json`, logs, attachments, and
   source changes are written under `~/hackbot/artifacts/<run_id>` on your host (no
   uploader runs locally).

   If your agent declares a `[source]`, the runtime automatically collects whatever it
   changed in the checkout — committed locally or not — into `changes/changes.patch`
   (an mbox that preserves each local commit's message and author, including binary and
   untracked files) plus a `changes/changes.json` summary. Apply it with one command:
   `git am changes/changes.patch`.

## Tracing (Weave)

Dashboards: [prod](https://wandb.ai/moz-bugbug/hackbot-prod/weave/agents), [dev](https://wandb.ai/moz-bugbug/hackbot-dev), [test](https://wandb.ai/moz-bugbug/hackbot-test)

Tracing is handled once in the runtime (`hackbot_runtime.tracing`), so every agent
gets it for free — there's nothing to add per agent. On startup the runtime calls
`weave.init()`, which autopatches the Claude Agent SDK (or other supported frameworks) and captures each query,
model response, and tool call, labelled with the agent's name (so the Weave
**Agents** view shows `build-repair`, `bug-fix`, etc. instead of a generic
`claude_agent_sdk`).

It is **opt-in**: the runtime only traces when it has W&B credentials, and never
fails a run if Weave can't start. `WEAVE_PROJECT` picks the destination project
(a bare `project` or `entity/project`; defaults to `hackbot-test`).

**Locally**, add the key to your root `.env`; the agent's `compose.yml` should
pass `WANDB_API_KEY` through to the agent container.

`.env`:

```dotenv
WANDB_API_KEY=...
```

`compose.yml`:

```yaml
environment:
  - WANDB_API_KEY=${WANDB_API_KEY:-}
```

**In deployment**, the agent container holds no long-lived key: it authenticates
via W&B [Identity Federation](https://docs.wandb.ai/platform/hosting/iam/identity_federation).

## Telling the platform what you need (`hackbot.toml`)

Think of `hackbot.toml` as your request to the platform: "please have these ready for me."
Everything is optional — only list what you actually use.

```toml
[source]                                # the platform shallow-clones and refreshes this for you
repo_url = "https://github.com/mozilla-firefox/firefox.git"

[firefox]                               # Firefox build paths, derived from the checkout
enabled = true
objdir = "objdir-ff-asan"
```

Everything else has a natural home: your agent's name and description go in `pyproject.toml`,
model and tool choices stay in code, and secrets and per-run inputs come from the environment.

## Building blocks you can reuse

Please reach for these instead of rolling your own — they're shared on purpose.

From **`hackbot-runtime`**:

- `HackbotContext, AgentError, HackbotAgentResult, run_async` — the pieces from the contract above.
- `from hackbot_runtime.claude import Reporter` — pretty-prints the agent's streamed messages
  to stdout and your log (call `reporter.header(...)` per work item, `reporter.message(msg)` per message).
- `from hackbot_runtime.actions.claude_sdk import actions_server_for` — gives you
  `(recorder, mcp_server)` so write-actions get recorded into `summary.json` rather than
  silently mutating the world.

Your actual **tools** (the things the model can call) come from **`agent-tools`**, each behind
its own extra (`[bugzilla]`, `[firefox]`):

```python
from agent_tools import bugzilla
from agent_tools.claude_sdk import build_sdk_server
server = build_sdk_server("bugzilla", BugzillaContext(client=...), bugzilla.TOOLS)
```

From there, you assemble your own `ClaudeAgentOptions` and drive the `ClaudeSDKClient` loop —
that part stays in your hands, where you want it.

## Creating your own agent

1. **Copy `bug-fix/`** as your starting point. Rename the folder, the distribution name in
   `pyproject.toml`, and the commands in `Dockerfile`/`compose.yml` (`python -m hackbot_agents.<name>`).
2. **Trim `hackbot.toml`** to just the `[source]`/`[firefox]` tables you need.
3. **Write your two modules:** `__main__.py` (`AgentInputs` + `main`) and `agent.py` (your logic
   plus a `HackbotAgentResult` subclass). Keep `<name_snake>/__init__.py` empty.
4. **Register it** in `services/hackbot-api/`: add a Pydantic input model in `app/schemas.py`,
   and a single `AGENT_REGISTRY` entry in `app/agents.py` (`name`/`description`/`job_name`/
   `input_schema`). Env vars are derived from your schema automatically (`bug_id` → `BUG_ID`),
   so there's no `build_env` to write — put deploy-time constants like broker URLs in the Job's
   static env instead.

And that's the whole recipe: one folder, one schema, one registry line. Welcome aboard!
