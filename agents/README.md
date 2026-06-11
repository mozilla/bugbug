# hackbot agents

Each subdirectory here is **one self-contained hackbot agent** — its logic,
entrypoint, and deployment live together. `bug-fix/` is the reference example.

## Anatomy of an agent (`agents/<name>/`)

```
agents/<name>/
  pyproject.toml          # dist "hackbot-agent-<name>"; packages = ["hackbot_agents"]; deps: hackbot-runtime[claude-sdk] + agent-specific
  hackbot.toml            # declares platform capabilities: [source], [firefox]
  Dockerfile              # multi-stage: builder / agent [/ broker]
  compose.yml             # local run; sets static env (e.g. the broker URL)
  hackbot_agents/         # shared PEP 420 namespace — NO __init__.py here
    <name_snake>/         # the agent package (e.g. bug_fix)
      __init__.py         # empty package marker
      agent.py            # run_<name>() logic + helpers (the reusable agent library)
      __main__.py         # entrypoint: AgentInputs(BaseSettings) + async def main(ctx) -> dict + run_async(main)
      prompts/ rules/     # assets read via Path(__file__).parent
      broker/             # OPTIONAL: secret-holding MCP sidecar (python -m hackbot_agents.<name>.broker)
  run_local.py            # OPTIONAL: run without Docker/broker for quick iteration
```

## `hackbot.toml` — what the platform provides

Declare the capabilities your agent needs in a `hackbot.toml` at the agent root
(alongside `pyproject.toml` / `Dockerfile`); the runtime prepares them and hands
you a single `HackbotContext`. Every table is optional — omit `[source]` if you
don't operate on a repo, omit `[firefox]` if you don't need a Firefox build.

```toml
[source]                                # the runtime clones/refreshes this for you
repo_url = "https://github.com/mozilla-firefox/firefox.git"
checkout_path = "/workspace/firefox"    # default; env SOURCE_REPO overrides

[firefox]                               # Firefox build paths, derived from the checkout
enabled = true
objdir = "objdir-ff-asan"
```

Agent identity (name/description) stays in `pyproject.toml`; model defaults and
tool allowlists stay in code; secrets and per-run inputs stay in the
environment. The toml holds only platform-capability declarations.

Every agent ships its package under the shared **`hackbot_agents` PEP 420 namespace**
(`hackbot_agents.<name_snake>`), so multiple agents installed into one environment never
collide. **Never add `hackbot_agents/__init__.py`** — the missing namespace-level
`__init__.py` is what lets the agent distributions merge instead of clobbering each other.

The runtime invokes the agent with `python -m hackbot_agents.<name>`, running
`hackbot_agents/<name>/__main__.py`. That module is the thin deployment wrapper:
it defines `AgentInputs(BaseSettings)`, an `async def main(ctx)`, and calls
`run_async(main)`. `run_async` auto-discovers `hackbot.toml` (cwd first — the
Dockerfile copies it into `/app` — then walks up from the entry module to the
agent root in an editable checkout) and exits the process with the run's status.
`main` validates inputs and calls the `run_<name>()` logic in `agent.py`,
reading everything the platform provides off `ctx` (`ctx.source_repo`,
`ctx.firefox`, `ctx.anthropic`, `ctx.actions`, `ctx.publish_file`).

## Shared building blocks (in `hackbot-runtime`)

Don't re-implement these — import them:

- `from hackbot_runtime import HackbotContext, AgentError, run_async` — the entrypoint
  contract. `main(ctx)` **returns a findings dict** (or `None`) on success, and **raises**
  to fail — `AgentError("…")` for an expected failure, any exception for a crash. The
  runtime turns that into `summary.json` (`status`/`error`/`findings`) and the process
  exit code; `run_async(main)` exits the process itself, so the entrypoint is just that
  one call. `HackbotContext` is the one object `main()` receives; it answers for the
  platform: `ctx.source_repo` (prepared from `[source]` on first access), `ctx.firefox`
  (a `FirefoxContext` from `[firefox]`), `ctx.anthropic.api_key` (validated), plus the
  results/artifacts/actions plumbing (`ctx.actions`, `ctx.publish_file`,
  `ctx.publish_json`).
- `from hackbot_runtime import ensure_source_repo` — the lower-level shallow-clone/refresh
  primitive (you normally don't call this directly; `ctx.source_repo` does it for you).
- `from hackbot_runtime.claude import Reporter` — renders streamed claude-agent-sdk
  messages to stdout/log. Call `reporter.header("...")` per work item, `reporter.message(msg)` per message.
- `from hackbot_runtime.actions.claude_sdk import actions_server_for` — returns
  `(recorder, mcp_server)`; write actions land in `summary.json` instead of mutating anything.

Reusable MCP **tool servers** live in the separate `agent-tools` package, each behind its
own optional extra (`agent-tools[bugzilla]`, `agent-tools[firefox]`). Import the domain
module and build the server via the adapter:

```python
from agent_tools import bugzilla
from agent_tools.claude_sdk import build_sdk_server
server = build_sdk_server("bugzilla", BugzillaContext(client=...), bugzilla.TOOLS)
```

You still assemble your own `ClaudeAgentOptions` and drive the `ClaudeSDKClient` loop —
those stay explicit and in your hands.

## Adding a new agent

1. `agents/<name>/hackbot.toml` — declare `[source]`/`[firefox]` if you need them
   (omit either otherwise).
2. `agents/<name>/hackbot_agents/<name_snake>/__main__.py` — define `AgentInputs(BaseSettings)`
   (domain inputs only), an `async def main(ctx: HackbotContext) -> dict` that returns
   findings on success and raises `AgentError` to fail, and end with `run_async(main)` (it
   discovers `hackbot.toml` — cwd, then up to the agent root — and exits the process itself).
3. `agents/<name>/hackbot_agents/<name_snake>/agent.py` — your prompts/logic, exposing the
   `run_<name>()` entrypoint `main` calls (leave `<name_snake>/__init__.py` empty). Do **not**
   create `agents/<name>/hackbot_agents/__init__.py`.
4. Copy `pyproject.toml`, `Dockerfile`, `compose.yml` from `bug-fix/` and rename (the
   Dockerfile CMDs become `python -m hackbot_agents.<name>` / `… .broker`, and it copy
   `agents/<name>/hackbot.toml` into `/app`).
5. In `services/hackbot-api/app/schemas.py`, add a Pydantic input model.
6. In `services/hackbot-api/app/agents.py`, add one `AGENT_REGISTRY` entry
   (`name` + `description` + `job_name` + `input_schema`). **No `build_env`** —
   env vars are derived from the schema by `model_to_env` (field `bug_id` → `BUG_ID`).
   Put deploy-time constants (broker URLs, etc.) in the Job's static env config, not the schema.

That's it: one folder + one schema + one registry line.
