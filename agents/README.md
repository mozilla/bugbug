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
