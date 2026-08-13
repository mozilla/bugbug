# hackbot-api

The control plane. FastAPI on Cloud Run, backed by Cloud SQL Postgres. It is the only
component that knows the agent catalog, and the only writer of run state.

## Endpoints

### Public — `X-API-Key`

| Method | Path                              | Does                                                   |
| ------ | --------------------------------- | ------------------------------------------------------ |
| GET    | `/agents`                         | The catalog, each with its input JSON schema           |
| POST   | `/agents/{agent}/runs`            | Validate inputs, create a run, start an execution      |
| GET    | `/runs`                           | List runs; filter by `agent`, `status`, `requested_by` |
| GET    | `/runs/{run_id}`                  | One run: status, inputs, summary, artifacts            |
| GET    | `/runs/{run_id}/artifacts/{path}` | A short-lived signed GCS download URL                  |
| GET    | `/runs/{run_id}/actions`          | Recorded actions and their apply state                 |
| POST   | `/runs/{run_id}/actions/apply`    | Apply all pending actions (idempotent)                 |
| GET    | `/health`                         | Health check                                           |

`POST /agents/{agent}/runs` accepts an `X-On-Behalf-Of` header carrying the requesting
user's email, stored as `requested_by` — the caller is a trusted service (the UI), so this
is attribution, not authentication.

Artifact downloads are restricted to artifacts already listed on the run, which both scopes
the download to that run's prefix and prevents probing unrelated objects.

### Inbound webhooks — HMAC signature

| POST | `/webhooks/phabricator` | `@hackbot` mention on a revision triggers a bug-fix run |

Authenticated by Phabricator's own HMAC signature over the raw body, so it sits on its own
router without the API-key dependency. See [triggers.md](triggers.md).

### Internal events — Google OIDC token

| POST | `/internal/events/agent-run-finished` | An execution reached a terminal state |
| POST | `/internal/events/apply-run-actions` | Consumer of `run.completed` |

Named for the domain outcome or the job they do, not the GCP mechanism that feeds them.

## Creating a run

```
validate payload against the agent's input schema  ── 422 on mismatch
mint a V4 signed POST policy scoped to runs/<run_id>/
insert Run(status=pending)
trigger a Cloud Run Job execution with env overrides on the `agent` container
store execution_name
```

Env overrides are the run id, the results bucket/prefix/policy, and the inputs mapped from
the schema. If the trigger fails the run is marked `failed` with the reason and the caller
gets a 502 — a run row always exists, so a failed dispatch is visible rather than lost.

## Run states

```
pending ──> running ──> succeeded
                   └──> failed
                   └──> timed_out
```

Terminal status is decided in `finalize_run` from the **execution status** and the
**`summary.json`** together:

| Execution     | `summary.json`   | Result                                |
| ------------- | ---------------- | ------------------------------------- |
| cancelled     | any              | `timed_out`                           |
| any           | missing          | `failed`                              |
| any           | `status != "ok"` | `failed`                              |
| not succeeded | `status == "ok"` | `failed` (exited non-zero despite ok) |
| succeeded     | `status == "ok"` | `succeeded`                           |

Requiring both to agree is deliberate: an OOM kill after a clean `summary.json` write, or a
crash before writing one, both land as failures rather than false successes.

## Completion detection

`GET /runs/{run_id}` is a plain database read. Completion is detected **out of band**:

```
Cloud Run Job execution completes
  → Cloud Logging sink on the `system_event` completion audit log
  → Pub/Sub push
  → POST /internal/events/agent-run-finished
  → finalize_run
```

The completion log fires for success and failure alike, including OOM and crash. The route
only has to identify _which_ run finished — `finalize_run` re-queries the authoritative
execution status rather than trusting the payload. Correlation is on `execution_name`, with
a suffix match as a fallback so a v1/v2 resource-name prefix mismatch doesn't break it.

`finalize_run` is idempotent via `finalized_at`, because push delivery is at-least-once. It
reads `summary.json`, lists the artifacts, sets the terminal state, and publishes
`run.completed`.

## Events

One topic per domain, `<domain>-events` — `agent-run-events` today. Events carry routing
keys as Pub/Sub **attributes** (`event_type`, `agent`, `status`) because subscription
filters can only match attributes, never the body; the JSON body carries the fuller
payload.

Publishing is best-effort and never raises: the `Run` row is durably committed first, so a
lost publish means a delayed downstream reaction, not lost primary state.

`run.completed` currently drives one consumer, `apply-run-actions`. Additional consumers
(notifications, outbound webhooks) get their **own route** named after their own job, and a
new event domain gets its own topic rather than overloading this one — keeping IAM,
retention and schema separable.

## Data model

**`runs`** — `run_id` (uuid, pk), `agent`, `status`, `inputs`, `requested_by`,
`execution_name`, `results_prefix`, `summary`, `artifacts`, `error`, `created_at`,
`updated_at`, `finalized_at`. Indexed on `agent`, `status`, `requested_by`, `created_at`;
listing orders by `created_at desc, run_id desc` so offset paging is stable when timestamps
collide.

**`run_actions`** — one row per entry in a run's `summary.json` actions, unique on
`(run_id, idx)`: `type`, `params`, `ref`, `status` (`pending`/`applied`/`failed`),
`result`, `error`, `applied_at`. See [actions.md](actions.md).

Schema changes go through Alembic (`services/hackbot-api/alembic/`).

## Local development

Commands and the full config reference are in [deployment.md](deployment.md). Two things
specific to this service:

- **`WEBHOOK_SECRET` has no default**, so a missing one fails at startup rather than
  silently accepting or rejecting deliveries.
- **Signing GCS URLs needs an impersonating credential** —
  `gcloud auth application-default login --impersonate-service-account=<sa>`. See
  [security.md](security.md) for why, and what the deployed service needs instead.
