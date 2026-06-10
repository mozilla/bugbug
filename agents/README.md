# hackbot agents

Each subdirectory here is **one self-contained hackbot agent** — its logic,
entrypoint, and deployment live together. `bug-fix/` is the reference example.

## Anatomy of an agent (`agents/<name>/`)

```
agents/<name>/
  pyproject.toml          # package "hackbot-agent-<name>"; deps: hackbot-runtime[claude-sdk] + agent-specific
  Dockerfile              # multi-stage: builder / agent [/ broker]
  compose.yml             # local run; sets static env (e.g. the broker URL)
  agent/                  # the agent: run_<name>() logic + prompts/, rules/, MCP servers
    __main__.py           # entrypoint: AgentInputs(BaseSettings); async def main(ctx) -> AgentResult; run_async(main)
  broker/                 # OPTIONAL: secret-holding MCP sidecar (e.g. holds the Bugzilla API key)
  run_local.py            # OPTIONAL: run without Docker/broker for quick iteration
```

The runtime invokes the agent with `python -m agent`. `agent/__main__.py` is the
thin deployment wrapper — it validates inputs, calls the `run_<name>()` logic in
`agent/__init__.py`, and passes `ctx.actions` (the recorder) plus the inputs into
it.

## Shared building blocks (in `hackbot-runtime`)

Don't re-implement these — import them:

- `from hackbot_runtime import Context, AgentResult, run_async` — the entrypoint contract.
- `from hackbot_runtime.claude import Reporter` — renders streamed claude-agent-sdk
  messages to stdout/log. Call `reporter.header("...")` per work item, `reporter.message(msg)` per message.
- `from hackbot_runtime.actions.claude_sdk import actions_server_for` — returns
  `(recorder, mcp_server)`; write actions land in `summary.json` instead of mutating anything.

Reusable MCP **tool servers** live in the separate `agent-tools` package, each
behind its own optional extra (`agent-tools[bugzilla]`, `agent-tools[firefox]`):

- `from agent_tools.bugzilla import BugzillaContext, build_server` — read-only Bugzilla MCP.
- `from agent_tools.firefox import FirefoxContext, build_server` — Firefox build/test MCP.

You still assemble your own `ClaudeAgentOptions` and drive the `ClaudeSDKClient`
loop — those stay explicit and in your hands.

## Adding a new agent

1. `agents/<name>/agent/__main__.py` — define `AgentInputs(BaseSettings)`,
   `async def main(ctx) -> AgentResult`, end with `raise SystemExit(run_async(main))`.
2. `agents/<name>/agent/__init__.py` — your prompts/logic, exposing an async entrypoint.
3. Copy `pyproject.toml`, `Dockerfile`, `compose.yml` from `bug-fix/` and rename.
4. In `services/hackbot-api/app/schemas.py`, add a Pydantic input model.
5. In `services/hackbot-api/app/agents.py`, add one `AGENT_REGISTRY` entry
   (`name` + `description` + `job_name` + `input_schema`). **No `build_env`** —
   env vars are derived from the schema by `model_to_env` (field `bug_id` → `BUG_ID`).
   Put deploy-time constants (broker URLs, etc.) in the Job's static env config, not the schema.

That's it: one folder + one schema + one registry line.
