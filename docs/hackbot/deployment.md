# Deployment and configuration

Everything runs on GCP in a Hackbot-only project.

| Component                | Runs as                                               | Deployed by                                 |
| ------------------------ | ----------------------------------------------------- | ------------------------------------------- |
| `hackbot-api`            | Cloud Run **service** + Cloud SQL Postgres            | Outside this repo                           |
| Agents                   | Cloud Run **Job**, one per agent                      | Outside this repo                           |
| Event wiring             | Logging sink, Pub/Sub topic + subscriptions, Eventarc | Outside this repo                           |
| `hackbot-ui`             | Cloud Run **service**                                 | `services/hackbot-ui/deploy.sh`             |
| `hackbot-pulse-listener` | Cloud Run **worker pool** (no HTTP)                   | `services/hackbot-pulse-listener/deploy.sh` |

**Only the UI and listener have deploy scripts in this repository.** Provisioning for the
API, the agent Jobs, and the event plumbing lives elsewhere; code comments referring to a
`deploy-events.sh` refer to that external tooling. If you add a component here, add its
deploy script alongside it and list it above.

Both scripts follow the same pattern: build, push to Artifact Registry, then deploy —
creating a dedicated least-privilege service account and granting it
`secretmanager.secretAccessor` on only the secrets it reads. Secret values are read from
env (so `source .env` works) and used only to seed a secret that does not exist yet —
existing secrets are never overwritten. Rotate with `gcloud secrets versions add`.

## The agent Job shape

An agent's Job manifest declares one or two containers per task, built as separate targets
of the same Dockerfile:

- **`agent`** — `python -m hackbot_agents.<name>`. No credentials. Receives the
  per-execution env overrides.
- **`broker`** (when the agent needs credentialed reads) —
  `python -m hackbot_agents.<name>.broker`. Holds the API keys, fully configured at deploy
  time, and is never touched by per-execution overrides.

The container name `agent` is what per-execution overrides target, so it must match.
`task_count=1`, timeout from `JOB_EXECUTION_TIMEOUT_SECONDS` (default 8h). `hackbot.toml` is
copied into the image's working directory, where the runtime discovers it.

Registering the agent in `AGENT_REGISTRY` requires the Job to exist under the `job_name`
given there.

## Environments

`ENVIRONMENT` (`development` / `production`) selects the Sentry environment and, in the
listener, the Pulse queue name — both local and prod authenticate as the same Pulse user, so
the queue name must vary or the two consumers steal each other's messages.

Weave tracing has its own per-environment projects (`hackbot-prod`, `hackbot-dev`,
`hackbot-test`) selected by `WEAVE_PROJECT` — see [tracing.md](tracing.md).

## Configuration reference

Each service parses its config once with `pydantic-settings` from env or `.env`. Nested
models bind from prefixed vars, splitting on the first underscore only
(`PHABRICATOR_API_KEY` → `phabricator.api_key`).

### hackbot-api

| Group       | Vars                                                                                      |
| ----------- | ----------------------------------------------------------------------------------------- |
| GCP         | `GCP_PROJECT`, `GCP_REGION`, `RESULTS_BUCKET`                                             |
| Database    | `CLOUD_SQL_INSTANCE`, `DB_USER`, `DB_PASS`, `DB_NAME`                                     |
| Jobs        | `JOB_EXECUTION_TIMEOUT_SECONDS`, `SIGNED_POLICY_MAX_BYTES`, `SIGNED_POLICY_GRACE_SECONDS` |
| Auth        | `EXTERNAL_API_KEY`, `PUSH_AUTH_AUDIENCE`, `PUSH_AUTH_SERVICE_ACCOUNT`                     |
| Phabricator | `PHABRICATOR_URL`, `PHABRICATOR_API_KEY`                                                  |
| Webhook     | `WEBHOOK_SECRET` (**required**), `WEBHOOK_BOT_PHID`, `WEBHOOK_MENTION_TOKEN`              |
| Events      | `RUN_EVENTS_TOPIC`                                                                        |
| Misc        | `HACKBOT_API_URL`, `PORT`, `ENVIRONMENT`, `SENTRY_DSN`                                    |

### Agent containers

Set by the platform per execution: `RUN_ID`, `RESULTS_BUCKET`, `RESULTS_PREFIX`,
`RESULTS_POLICY_URL`, `RESULTS_POLICY_FIELDS`, plus one var per input-schema field
(`BUG_ID`, `FAILURE_TASKS`, …).

Set at deploy time: `BROKER_URL`, `SOURCE_REPO`, the Anthropic federation ids
(`ANTHROPIC_FEDERATION_RULE_ID`, `ANTHROPIC_ORGANIZATION_ID`,
`ANTHROPIC_SERVICE_ACCOUNT_ID`, `ANTHROPIC_WORKSPACE_ID`), `WEAVE_PROJECT`.

Local only: `ANTHROPIC_API_KEY`, `WANDB_API_KEY`, `ARTIFACTS_DIR`.

### hackbot-ui

`HACKBOT_API_URL`, `HACKBOT_API_KEY`, `BETTER_AUTH_URL`, `BETTER_AUTH_SECRET`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`. Every instance must share
`BETTER_AUTH_SECRET` — that is the only shared session state.

### hackbot-pulse-listener

`PULSE_USER`, `PULSE_PASSWORD`, `HACKBOT_API_URL`, `HACKBOT_API_KEY`, `HACKBOT_UI_URL`,
`WATCHED_REPOS`, plus the filter tuning (`MAX_PUSH_AGE_HOURS`,
`TREEHERDER_CLASSIFICATION_WAIT_SECONDS`, `MAX_TEST_REPAIRS_PER_DAY`, …) and SendGrid
notification settings. `DRY_RUN=true` logs intended calls without POSTing. See its
[README](../../services/hackbot-pulse-listener/README.md).

## Running locally

The repo is a `uv` workspace (`agents/*`, `libs/*`, `services/*`), so everything shares one
lockfile.

**An agent**, exactly as it ships, via its `compose.yml`:

```bash
# .env at the repo root: ANTHROPIC_API_KEY, BUGZILLA_API_URL, BUGZILLA_API_KEY, ...
BUG_ID=1234567 docker compose up bug-fix-agent --build
```

Compose starts the broker alongside the agent, just like the Job does. With no upload
policy configured, `summary.json`, logs, attachments and the source patch are written to
`~/hackbot/artifacts/<run_id>` on the host under the same keys they'd have in GCS. Apply the
agent's changes with `git am changes/changes.patch`.

Add a new agent's `compose.yml` to the root `docker-compose.yml` `include:` list.

**The services:**

```bash
uv run --package hackbot-api uvicorn app.main:app --reload
uv run --package hackbot-pulse-listener python -m app
cd services/hackbot-ui && npm install && npm run dev
```

**Tests:**

```bash
uv run pytest libs/hackbot-runtime/tests
uv run --package hackbot-api pytest services/hackbot-api/tests
uv run --package hackbot-pulse-listener pytest services/hackbot-pulse-listener/tests
```

## Observability

- **Traces** — Weave, one project per environment. [tracing.md](tracing.md).
- **Errors** — Sentry, per service, tagged with environment and release.
- **Logs** — Cloud Logging. The same completion logs that drive finalization are the record
  of every execution.
- **Run history** — the `runs` and `run_actions` tables are the system of record, queryable
  through `GET /runs`.
