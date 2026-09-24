# Tracing (Weave)

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
