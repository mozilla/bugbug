# hackbot agents

Each subdirectory here is **one self-contained hackbot agent** — its logic,
entrypoint, and deployment live together. `bug-fix/` is the reference example.

## Anatomy of an agent (`agents/<name>/`)

```
agents/<name>/
  pyproject.toml          # dist "hackbot-agent-<name>"; packages = ["hackbot_agents"]; deps: hackbot-runtime[claude-sdk] + agent-specific
  Dockerfile              # multi-stage: builder / agent [/ broker]
  compose.yml             # local run; sets static env (e.g. the broker URL)
  hackbot_agents/         # shared PEP 420 namespace — NO __init__.py here
    <name_snake>/         # the agent package (e.g. bug_fix)
      __init__.py         # run_<name>() logic + helpers
      __main__.py         # entrypoint: AgentInputs(BaseSettings); async def main(ctx) -> AgentResult; run_async(main)
      prompts/ rules/     # assets read via Path(__file__).parent
      broker/             # OPTIONAL: secret-holding MCP sidecar (python -m hackbot_agents.<name>.broker)
  run_local.py            # OPTIONAL: run without Docker/broker for quick iteration
```

Every agent ships its package under the shared **`hackbot_agents` PEP 420 namespace**
(`hackbot_agents.<name_snake>`), so multiple agents installed into one environment never
collide. **Never add `hackbot_agents/__init__.py`** — the missing namespace-level
`__init__.py` is what lets the agent distributions merge instead of clobbering each other.

The runtime invokes the agent with `python -m hackbot_agents.<name>`.
`hackbot_agents/<name>/__main__.py` is the thin deployment wrapper — it validates inputs,
calls the `run_<name>()` logic in `__init__.py`, and passes `ctx.actions` (the recorder)
plus the inputs into it.

## Shared building blocks (in `hackbot-runtime`)

Don't re-implement these — import them:

- `from hackbot_runtime import Context, AgentResult, run_async` — the entrypoint contract.
- `from hackbot_runtime import ensure_source_repo` — shallow-clone/refresh a source repo.
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

1. `agents/<name>/hackbot_agents/<name_snake>/__main__.py` — define `AgentInputs(BaseSettings)`,
   `async def main(ctx) -> AgentResult`, end with `raise SystemExit(run_async(main))`.
2. `agents/<name>/hackbot_agents/<name_snake>/__init__.py` — your prompts/logic, exposing an
   async entrypoint. Do **not** create `agents/<name>/hackbot_agents/__init__.py`.
3. Copy `pyproject.toml`, `Dockerfile`, `compose.yml` from `bug-fix/` and rename (the
   Dockerfile CMDs become `python -m hackbot_agents.<name>` / `… .broker`).
4. In `services/hackbot-api/app/schemas.py`, add a Pydantic input model.
5. In `services/hackbot-api/app/agents.py`, add one `AGENT_REGISTRY` entry
   (`name` + `description` + `job_name` + `input_schema`). **No `build_env`** —
   env vars are derived from the schema by `model_to_env` (field `bug_id` → `BUG_ID`).
   Put deploy-time constants (broker URLs, etc.) in the Job's static env config, not the schema.

That's it: one folder + one schema + one registry line.
