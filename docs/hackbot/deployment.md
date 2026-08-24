# Deployment and configuration

Everything runs on GCP in a Hackbot-only project.

| Component                | Runs as                                               | Deployed by                                                                                  |
| ------------------------ | ----------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `hackbot-api`            | Cloud Run **service** + Cloud SQL Postgres            | Outside this repo                                                                            |
| Agents                   | Cloud Run **Job**, one per agent                      | Outside this repo                                                                            |
| Event wiring             | Logging sink, Pub/Sub topic + subscriptions, Eventarc | Outside this repo                                                                            |
| `hackbot-ui`             | Cloud Run **service**                                 | [services/hackbot-ui/deploy.sh](../../services/hackbot-ui/deploy.sh)                         |
| `hackbot-pulse-listener` | Cloud Run **worker pool** (no HTTP)                   | [services/hackbot-pulse-listener/deploy.sh](../../services/hackbot-pulse-listener/deploy.sh) |

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

Every service parses its config once with `pydantic-settings`, so its `Settings` class is
the authoritative list of what it reads — defaults included, which is the part that drifts
fastest:

| Service                  | Read the config from                                                                                                                 |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| `hackbot-api`            | [services/hackbot-api/app/config.py](../../services/hackbot-api/app/config.py)                                                       |
| `hackbot-pulse-listener` | [services/hackbot-pulse-listener/app/config.py](../../services/hackbot-pulse-listener/app/config.py)                                 |
| `hackbot-ui`             | [services/hackbot-ui/.env.example](../../services/hackbot-ui/.env.example)                                                           |
| Agent containers         | [libs/hackbot-runtime/hackbot_runtime/context.py](../../libs/hackbot-runtime/hackbot_runtime/context.py) + the agent's `AgentInputs` |

Two things those files do not tell you:

- **Nested models bind from prefixed vars**, splitting on the first underscore only:
  `PHABRICATOR_API_KEY` → `phabricator.api_key`, `WEBHOOK_SECRET` → `webhook.secret`,
  `BUGZILLA_WEBHOOK_SECRET` → `bugzilla_webhook.secret`, `SLACK_SIGNING_SECRET` →
  `slack.signing_secret`.
- **A prefix does not make a var a setting.** `SLACK_BOT_TOKEN` is not part of that nested
  model: the apply-side handler in `hackbot-runtime` reads it straight from the environment.
  The two sit side by side on hackbot-api and point opposite ways — the token posts
  messages, the signing secret verifies clicks coming back — so a deployment that posts fine
  can still reject every interaction.
- **An agent container's env arrives from three places.** Per-execution overrides from the
  API (`RUN_ID`, the results bucket/prefix/policy, one var per input-schema field); static
  Job env fixed at deploy time (`BROKER_URL`, `SOURCE_REPO`, the Anthropic federation ids,
  `WEAVE_PROJECT`); and local-only fallbacks (`ANTHROPIC_API_KEY`, `WANDB_API_KEY`,
  `ARTIFACTS_DIR`). Only the first varies per run.

Action handlers run inside `hackbot-api` and read their own credentials straight from its
env, so they are not in its `Settings`: `SLACK_BOT_TOKEN` for `slack.post_message`, and
`SENDGRID_API_KEY` + `NOTIFICATION_SENDER` for `email.send` (plus the optional
`NOTIFICATION_TEAM_EMAIL` and `NOTIFICATION_OVERRIDE_EMAIL` -- see
[actions.md](actions.md)). A handler whose credentials are missing fails its action rather
than the run.

The listener also honours `DRY_RUN=true`, which logs intended calls without POSTing them.

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

Recorded actions are applied by hackbot-api against Cloud SQL, so a local run leaves
them in `summary.json` unapplied.

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
