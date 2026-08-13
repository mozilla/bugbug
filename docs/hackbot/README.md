# Hackbot

Hackbot is a platform for running **autonomous agents against Mozilla's engineering
systems**: Firefox source, Bugzilla, Phabricator, Taskcluster CI, TestRail.

An agent is a container that gets started with a small set of inputs, investigates
something, and exits. It reports what it found, and — crucially — it does not change
the outside world while it runs. It _records_ what it wants to change; the platform
applies that afterwards. Everything else in the platform exists to make that shape
work: start the container, give it what it needs, collect what it produced, apply
what it proposed.

## The life of a run

```
  trigger                control plane                 execution
  ───────                ─────────────                 ─────────

  hackbot-ui  ─┐
  pulse-       ├──> hackbot-api ──> Cloud Run Job ──> ┌──────────┬─────────────┐
   listener   ─┤    (validate,      (one execution    │  agent   │   broker    │
  Phabricator ─┘     record Run,     per run)         │ (no      │ (holds keys,│
   webhook           mint upload                      │  creds)  │  if needed) │
                     policy)                          └────┬─────┴─────────────┘
                          ▲                                │
                          │                          artifacts +
                    run finished                      summary.json
                     (Pub/Sub)                             │
                          │                                ▼
                          └──────────────────────  GCS results bucket
                          │
                          ├─> finalize: read summary, list artifacts,
                          │            set terminal status
                          └─> apply recorded actions (Bugzilla comment,
                                       Phabricator revision, Slack, ...)
```

1. **Trigger.** Someone or something calls `POST /agents/{name}/runs` with inputs.
2. **Dispatch.** hackbot-api validates the inputs, records a `Run`, mints a signed
   upload policy scoped to that run, and starts one Cloud Run Job execution.
3. **Run.** The runtime inside the container prepares what the agent declared it needs
   (source checkout, Firefox build paths, model credentials), calls the agent's
   `main()`, and writes `summary.json` plus artifacts to the results bucket.
4. **Finalize.** A completion event brings the run to a terminal state and publishes
   `run.completed`.
5. **Apply.** The actions the agent recorded become real API calls — automatically for
   opted-in agents, on demand from the UI otherwise.

## Where to read next

| If you want to…                                            | Read                               |
| ---------------------------------------------------------- | ---------------------------------- |
| Understand the components and why they're split that way   | [architecture.md](architecture.md) |
| Write or modify an agent                                   | [agents.md](agents.md)             |
| Know what the runtime hands your agent                     | [runtime.md](runtime.md)           |
| Find a tool your agent can call, or add one                | [tools.md](tools.md)               |
| Understand how agents change the world (record-then-apply) | [actions.md](actions.md)           |
| Work on the control-plane service                          | [api.md](api.md)                   |
| Know how runs get started                                  | [triggers.md](triggers.md)         |
| Reason about credentials and trust boundaries              | [security.md](security.md)         |
| Deploy, configure, or run things locally                   | [deployment.md](deployment.md)     |
| Look at traces of a run                                    | [tracing.md](tracing.md)           |

## Code map

| Path                                                | What it is                                                          |
| --------------------------------------------------- | ------------------------------------------------------------------- |
| `libs/hackbot-runtime/`                             | The in-container runtime: agent contract, context, results, actions |
| `libs/agent-tools/`                                 | The tools a model can call: declarations + per-framework adapters   |
| `libs/phabricator-client/`, `libs/testrail-client/` | Shared API clients                                                  |
| `agents/<name>/`                                    | One self-contained agent: logic, image, local compose               |
| `services/hackbot-api/`                             | Control plane (FastAPI): runs, artifacts, actions, webhooks         |
| `services/hackbot-ui/`                              | Web UI (Next.js): trigger, observe, review and apply actions        |
| `services/hackbot-pulse-listener/`                  | Watches Taskcluster CI failures and dispatches repair runs          |

## Conventions in these docs

These docs cover **design and integration** — the contracts between the parts and the
decisions worth knowing before changing something — and stop short of restating code. Each
fact lives in one file; the others link to it.

Every PR that changes the Hackbot runtime, deployment, or agents should leave these docs
true.
