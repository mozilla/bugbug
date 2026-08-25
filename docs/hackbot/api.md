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

### Inbound webhooks — their own authentication

| Method | Path                           | Does                                                    |
| ------ | ------------------------------ | ------------------------------------------------------- |
| POST   | `/webhooks/phabricator`        | `@hackbot` mention on a revision triggers a bug-fix run |
| POST   | `/webhooks/bugzilla`           | `needinfo?` on the bot account triggers a bug-fix run   |
| POST   | `/webhooks/slack/interactions` | A click on an interactive element of a hackbot message  |

None of them uses the API key, so each sits on its own router without that dependency:
these senders cannot send an `X-API-Key`. Phabricator and Slack are authenticated by their
own HMAC signature over the raw body; Bugzilla by a shared secret in
`X-Bugzilla-Webhook-Secret`. Phabricator and Bugzilla answer `202` with
`{"status": "ignored", ...}` for a well-authenticated delivery that doesn't qualify, so BMO
and Phabricator don't retry it. Both are covered in [triggers.md](triggers.md).

**Slack interactions** all arrive on this one route: Slack posts every click on every
interactive element to the single Request URL configured for Interactivity, so the receiver
demultiplexes on the element's `action_id`. The path names the feature because Slack
configures one Request URL **per feature** — Event Subscriptions and Slash Commands are
separate URLs with their own payload shapes, and they would get their own routes beside this
one rather than sharing it. Three things about the delivery shape the route:

- The body is **not JSON**. It is `application/x-www-form-urlencoded` with the JSON in a
  single `payload` field, parsed from the same raw bytes the signature covers.
- **Slack expects a response within 3 seconds** and shows the person who clicked an error if
  it does not arrive, so real work belongs off the request: publish an event and answer the
  message afterwards through the delivery's `response_url` or `chat.update`.
- **A delivery this cannot act on is answered `200` and logged**, not `4xx`/`5xx`. Slack
  retries a non-2xx, and a payload that cannot be parsed will not parse on retry either,
  so refusing it only shows a failure nobody can fix.

Nothing posts an interactive element yet, so nothing reaches this route in practice: it
authenticates a delivery, parses it, and records that it happened. Acting on a click, and
the authorization that has to come first, lands with the first button. A click that starts
a run will then appear in [triggers.md](triggers.md).

What the endpoint answers:

| Delivery                                           | Status |
| -------------------------------------------------- | ------ |
| Signature verifies                                 | `200`  |
| A signature header is absent (a malformed request) | `422`  |
| Both headers present, signature or freshness fails | `401`  |
| Signed, but the payload cannot be understood       | `200`  |

The two signature headers are declared required, so an absent one is a validation failure
rather than an authentication one. Slack always sends both, so a delivery missing them is
not a Slack delivery.

Turning it on is Slack-app config, not a deploy: **Interactivity & Shortcuts → Request URL**
= `https://<hackbot-api-host>/webhooks/slack/interactions`, and `SLACK_SIGNING_SECRET` from
**Basic Information → App Credentials**. Interactivity needs no new OAuth scopes, so no
workspace reinstall. The secret is not optional: without a usable one the service does not
start at all (see below).

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

Two tables, defined in [app/database/models.py](../../services/hackbot-api/app/database/models.py); schema changes go through Alembic
([services/hackbot-api/alembic/](../../services/hackbot-api/alembic/)).

**`runs`** is the system of record for a run — its inputs, execution name, summary,
artifacts and terminal state. Listing orders by `created_at desc, run_id desc` rather than
timestamp alone, so offset paging stays stable when two runs share a timestamp.

**`run_actions`** holds one row per entry in a run's `summary.json` actions, unique on
`(run_id, idx)`, carrying that action's apply state. That uniqueness is what makes replays
idempotent — see [actions.md](actions.md).

## Local development

Commands and the full config reference are in [deployment.md](deployment.md). Two things
specific to this service:

- **`WEBHOOK_SECRET` and `SLACK_SIGNING_SECRET` have no defaults**, so a missing one fails
  at startup rather than silently accepting or rejecting deliveries. Both are HMAC keys for
  an inbound receiver: an empty one would mean either accepting every delivery
  unauthenticated or rejecting every real one, and neither is a state worth booting into.
  `SLACK_SIGNING_SECRET` is validated **non-blank** rather than merely present, so `=""`
  fails at startup too, and every consumer downstream can take a usable key for granted
  instead of carrying an unconfigured case.
- **Signing GCS URLs needs an impersonating credential** —
  `gcloud auth application-default login --impersonate-service-account=<sa>`. See
  [security.md](security.md) for why, and what the deployed service needs instead.
