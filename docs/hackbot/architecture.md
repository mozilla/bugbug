# Architecture

## Components

**`hackbot-api`** — the control plane. The only component that knows which agents exist,
what inputs they take, and what state their runs are in. Owns the Postgres database,
starts executions, and applies recorded actions. FastAPI on Cloud Run.

**Agent images** — one subdirectory of [agents/](../../agents/) per agent, each a container image deployed as a Cloud
Run **Job**; a run is one execution of that Job. See [agents.md](agents.md).

**`hackbot-runtime`** — the library inside the agent container. It owns everything that
is the same for every agent: loading config, preparing the source checkout, providing
model credentials, capturing source changes, collecting recorded actions, writing
`summary.json`, and tracing. Agent authors write logic, not plumbing.

**`hackbot-ui`** — the human surface. Trigger runs, watch them, read findings, download
artifacts, review and apply recorded actions, retrigger failures.

**`hackbot-pulse-listener`** — an always-on Cloud Run worker pool that watches
Taskcluster failures and dispatches build-repair / test-repair runs. It is a _client_ of
hackbot-api, not part of it.

**`agent-tools`** — the tools an agent's model can call, declared once and adapted per
framework. Read tools (Bugzilla, Phabricator, Searchfox, Firefox build, VCS) live here;
write-actions live in the runtime. See [tools.md](tools.md).

## The main design decisions

### Agents are one-shot jobs, not services

A run clones Firefox, maybe builds it, reasons for a while, and exits. That is a batch
workload with a wide latency spread (minutes to hours), so agents are Cloud Run **Jobs**
rather than request-serving services: no idle cost, per-execution isolation, generous
timeout (default 8h). A crashed or OOM-killed run is just a failed execution, and the
platform learns about it the same way it learns about a clean exit.

The consequence to keep in mind: **nothing survives a run**. The checkout, the build,
the logs — all gone when the container exits. Anything worth keeping must be published
as an artifact.

### `summary.json` is the whole agent→platform contract

An agent reports by returning a result object or raising. The runtime turns that into
exactly one file:

```json
{
  "status": "ok" | "error",
  "error": null,
  "findings": { ... },      // the agent's own result model
  "actions":  [ ... ]       // what it wants the platform to do
}
```

plus an exit code. Everything downstream — the terminal status, the UI, the applier, the
notification emails — reads only this. The runtime writes it on **every** path, including
when the agent raises, so a failed run is still explainable.

This is why the API can be indifferent to what an agent actually does. Adding an agent
does not touch the run lifecycle.

### The agent container holds no credentials

It is the least-trusted component in the system, so it gets no durable credential. Three
mechanisms, one principle — rationale and details in [security.md](security.md):

- **Third-party API keys** (Bugzilla, Phabricator) live in a **broker sidecar**.
- **Model and tracing credentials** come from **Workload Identity Federation** — the
  container exchanges its own Google identity for short-lived tokens.
- **Writing results** is a **signed GCS POST policy**, per run, scoped to that run's prefix.

### Agents propose; the platform disposes

An agent never posts a Bugzilla comment or creates a Phabricator revision while it runs. It
records the intent into `summary.json`; hackbot-api turns those records into real API calls
once the run reaches a verified-good terminal state. See [actions.md](actions.md) for what
that buys and how it works.

### Configuration is split by who owns it

| Where                                         | What                                                            | Changes when            |
| --------------------------------------------- | --------------------------------------------------------------- | ----------------------- |
| `agents/<name>/hackbot.toml`                  | Capabilities the agent needs prepared (`[source]`, `[firefox]`) | The agent changes       |
| `AGENT_REGISTRY` + input schema (hackbot-api) | The agent's public input contract                               | The agent's API changes |
| Cloud Run Job env / Secret Manager            | Deploy-time constants and secrets                               | The deployment changes  |
| Per-execution env overrides                   | This run's inputs                                               | Every run               |

Per-run inputs are derived from the Pydantic input schema automatically
(`bug_id` → `BUG_ID`), so registering an agent is one schema plus one registry entry — no
per-agent env-mapping code.

### Tool declarations are framework-neutral

Tools are declared as plain `@tool`-decorated handlers that import no agent framework;
adapters render them for a specific one (claude-agent-sdk today). One declaration therefore
backs a read tool, a recorded write-action, and an in-process or brokered MCP server alike.
See [tools.md](tools.md).

### Local runs and deployed runs take the same path

The runtime's only branch on environment is "is there an uploader configured?" — if not,
artifacts are written to disk under the same keys they'd have in GCS. So
`docker compose up <agent>` exercises what production exercises, minus the upload.

### Platform-specific glue is isolated at the edges

Cloud Run and Eventarc appear in narrow, named places: the job trigger, the completion-log
parser, the push-auth check. The routes that consume them are named after the domain
outcome (`agent-run-finished`, `apply-run-actions`), not the mechanism, and the finalize
logic re-queries authoritative status rather than trusting the event payload. Moving or
adding an execution platform means adding a payload parser, not rewriting the lifecycle.

## What is _not_ in this repository

The deploy scripts for **hackbot-api**, the **agent Cloud Run Jobs**, and the **Pub/Sub
topics, subscriptions and Eventarc triggers** are managed outside this repo. Only
[services/hackbot-ui/deploy.sh](../../services/hackbot-ui/deploy.sh) and [services/hackbot-pulse-listener/deploy.sh](../../services/hackbot-pulse-listener/deploy.sh) live here.
See [deployment.md](deployment.md).
