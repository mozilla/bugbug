# Tracing (Weave)

Dashboards: [prod](https://wandb.ai/moz-bugbug/hackbot-prod/weave/agents), [dev](https://wandb.ai/moz-bugbug/hackbot-dev), [test](https://wandb.ai/moz-bugbug/hackbot-test)

Tracing is handled once in the runtime (`hackbot_runtime.tracing`), so every agent
gets it for free — there's nothing to add per agent. On startup the runtime calls
`weave.init()`, which autopatches the Claude Agent SDK (or other supported frameworks) and captures each query,
model response, and tool call, labelled with the agent's name (so the Weave
**Agents** view shows `build-repair`, `bug-fix`, etc. instead of a generic
`claude_agent_sdk`). Subagents are traced inside their parent agent, see
[Subagents](#subagents).

It is **opt-in**: the runtime only traces when it has W&B credentials, and never
fails a run if Weave can't start. `WEAVE_PROJECT` picks the destination project
(a bare `project` or `entity/project`; defaults to `hackbot-test`).

## Linking a run to its traces

The runtime tags every span with the hackbot run id (surfaced by Weave as
`attributes.hackbot.run_id`, see [tracing.py](../../libs/hackbot-runtime/hackbot_runtime/tracing.py)).
The Hackbot UI links each run page to the Weave **Agents** view filtered on that
attribute, so all of a run's conversations show up together. The UI's
`WEAVE_PROJECT` (`entity/project`, default `moz-bugbug/hackbot-dev`) must point at
the project the agents trace into.

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
via W&B [Identity Federation](https://docs.wandb.ai/platform/hosting/iam/identity_federation),
the same mechanism the runtime uses for Anthropic — see [security.md](security.md).

## Subagents

Subagents (e.g. bug-fix's `investigator`) are part of their parent agent's run,
not separate agents: they don't appear in the **Agents** view, and their cost
counts towards the parent's.

In a run's trace tree:

```text
invoke_agent bug-fix                      the run's turn
├── chat / execute_tool ...               the main agent's calls
├── invoke_agent bug-fix                  a subagent, hackbot.subagent=investigator
│   └── chat / execute_tool ...           its calls, hackbot.subagent=investigator
├── invoke_agent bug-fix                  a parallel subagent, hackbot.subagent=duplicate_hunter
│   └── ...
└── chat claude-opus-5-5                  subagent token usage, hackbot.subagent=duplicate_hunter,investigator
```

The subagent's description and prompt are on its `invoke_agent` span.

### Attributes

| Attribute                     | Set on                                    | Use                                                             |
| ----------------------------- | ----------------------------------------- | --------------------------------------------------------------- |
| `attributes.hackbot.run_id`   | every span                                | all spans of one run                                            |
| `attributes.hackbot.subagent` | a subagent's span and everything under it | one subagent's work                                             |
| `attributes.hackbot.subagent` | the usage `chat` spans under the turn     | the subagents' cost, comma-separated if several ran in the turn |

For example, filter on `attributes.hackbot.subagent` containing `investigator` to
see what the investigator did, how long it took and what it cost, or exclude it
to see the main agent alone.

### Token usage and cost

The SDK reports a subagent's token usage only as part of the turn's per-model
totals. The runtime records that usage on an extra `chat` span per model at the
end of each turn (see `_trace_subagents_as` in
[tracing.py](../../libs/hackbot-runtime/hackbot_runtime/tracing.py)), so:

- The run's total cost in Weave matches the SDK's `total_cost_usd`.
- The subagents' own `chat` spans show no tokens.
- When several subagents run in one turn, their cost is combined on one span
  and can't be split between them.
- The span also includes the CLI's small helper model calls (e.g. Haiku), which
  Weave may not price.

## Querying traces from AI assistants

Agent developers can let Claude Code (or other AI) read the traces via the W&B MCP server and skills.

1. Get an invite to the `moz-bugbug` W&B team (Weave access is invite-only).
2. Create an API key, following the [W&B AI assistants guide](https://docs.coreweave.com/products/wandb/platform/ai-assistants).
3. Install the skills and the MCP server:

```bash
export WANDB_API_KEY="<key>"
export WANDB_PROJECT=hackbot-dev
npx skills add wandb/skills --skill '*' --yes
claude mcp add --transport http wandb https://mcp.withwandb.com/mcp --header "Authorization: Bearer <key>"
```

Example query:

```bash
claude "analyze Weave agent traces for the test-repair agent for the last week where the agent used model Opus 5.5. See where the bottlenecks are and how to reduce duration of the runs"
```
