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
