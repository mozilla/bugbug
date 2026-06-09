# hackbot agents

Each subdirectory here is **one self-contained hackbot agent** — its logic,
entrypoint, and deployment live together. `bug-fix/` is the reference example.

## Anatomy of an agent (`agents/<name>/`)

```
agents/<name>/
  pyproject.toml          # package "hackbot-agent-<name>"; deps: hackbot-runtime[claude-sdk] + agent-specific
  Dockerfile              # multi-stage: builder / agent [/ broker]
  compose.yml             # local run; sets static env (e.g. the broker URL)
  agent_runner/
    __main__.py           # AgentInputs(BaseSettings); async def main(ctx) -> AgentResult; run_async(main)
  agent/                  # the agent's brain: run_bug_fix()-style entrypoint + prompts/, rules/, MCP servers
  broker/                 # OPTIONAL: secret-holding MCP sidecar (e.g. holds the Bugzilla API key)
  run_local.py            # OPTIONAL: run without Docker/broker for quick iteration
```

`agent_runner` is the thin deployment wrapper the runtime invokes; `agent/` is
the actual logic. The runner does `from agent import run_<name>` and passes
`ctx.actions` (the recorder) plus the validated inputs into it.

## Shared building blocks (in `hackbot-runtime`)

Don't re-implement these — import them:

- `from hackbot_runtime import Context, AgentResult, run_async` — the entrypoint contract.
- `from hackbot_runtime.claude import Reporter` — renders streamed claude-agent-sdk
  messages to stdout/log. Call `reporter.header("...")` per work item, `reporter.message(msg)` per message.
- `from hackbot_runtime.actions.claude_sdk import actions_server_for` — returns
  `(recorder, mcp_server)`; write actions land in `summary.json` instead of mutating anything.
- `from hackbot_runtime.mcp.bugzilla import BugzillaContext, build_server` — read-only Bugzilla MCP.

You still assemble your own `ClaudeAgentOptions` and drive the `ClaudeSDKClient`
loop — those stay explicit and in your hands.

## Adding a new agent

1. `agents/<name>/agent_runner/__main__.py` — define `AgentInputs(BaseSettings)`,
   `async def main(ctx) -> AgentResult`, end with `raise SystemExit(run_async(main))`.
2. `agents/<name>/agent/` — your prompts/logic, exposing an async entrypoint.
3. Copy `pyproject.toml`, `Dockerfile`, `compose.yml` from `bug-fix/` and rename.
4. In `services/hackbot-api/app/schemas.py`, add a Pydantic input model.
5. In `services/hackbot-api/app/agents.py`, add one `AGENT_REGISTRY` entry
   (`name` + `description` + `job_name` + `input_schema`). **No `build_env`** —
   env vars are derived from the schema by `model_to_env` (field `bug_id` → `BUG_ID`).
   Put deploy-time constants (broker URLs, etc.) in the Job's static env config, not the schema.

That's it: one folder + one schema + one registry line.
