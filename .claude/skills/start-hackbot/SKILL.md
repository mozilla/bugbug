---
name: start-hackbot
description: How to start / run the hackbot services locally — the web UI (hackbot-ui), the API (hackbot-api), and the pulse listener. Use when asked to run, start, launch, boot, or "try" hackbot, the hackbot UI, the hackbot dashboard, or hackbot-api locally.
---

# Starting hackbot

Hackbot is three cooperating services under `services/`:

| Service                  | What it is                                                                                 | Runtime          |
| ------------------------ | ------------------------------------------------------------------------------------------ | ---------------- |
| `hackbot-api`            | FastAPI orchestrator; triggers agents as Cloud Run Jobs, stores runs in Cloud SQL Postgres | Python (uvicorn) |
| `hackbot-ui`             | Next.js "Launchpad" — trigger runs, browse "Recent runs", read results                     | Node (Next.js)   |
| `hackbot-pulse-listener` | Listens to Firefox CI build failures and triggers `build-repair` runs via the API          | Python           |

The UI talks only to the API (server-side, via `X-API-Key`); the API owns the DB, GCS, and Cloud Run Jobs.

Pick the path that matches the request:

- **"Try the UI / see the dashboard" without GCP creds** → [Quick local UI demo](#quick-local-ui-demo). Fastest; no Cloud SQL / GCP needed (a Google OAuth client still is — sign-in always applies).
- **Run the real API** → [Run hackbot-api](#run-hackbot-api). Needs GCP + Cloud SQL config.
- **Run the real UI against a real API** → [Run hackbot-ui](#run-hackbot-ui). Needs Google OAuth.
- **Run the pulse listener** → [Run the pulse listener](#run-the-pulse-listener).

---

## Quick local UI demo

Runs the **real** UI (proxy routes + components) against a throwaway stub that stands in for
hackbot-api, so the "Recent runs" view can be inspected without Cloud SQL / GCP. **Sign-in
still applies**: there is no auth bypass, so this path needs a Google OAuth client (a personal
throwaway one is fine — see `services/hackbot-ui/README.md`).

Requirements: `python3`, `node`/`npm`.

1. **Stub API** — a small stdlib HTTP server that serves `GET /agents`, `GET /runs`
   (honoring `agent`/`status`/`author`/`limit`/`offset`), and `GET /runs/{run_id}` with seeded
   fake runs. It lives at `services/hackbot-ui/dev/stub_api.py`; run it on :8080:

   ```bash
   # Optional: attribute the seeded "my runs" to the account you sign in with.
   HACKBOT_DEV_USER=you@mozilla.com python3 services/hackbot-ui/dev/stub_api.py   # 127.0.0.1:8080
   ```

   If that file isn't in the checkout, the stub hasn't landed there yet — use
   [Run hackbot-api](#run-hackbot-api) instead.

2. **UI env** — create `services/hackbot-ui/.env.local` (gitignored), pointing at the stub:

   ```bash
   HACKBOT_API_URL=http://127.0.0.1:8080
   HACKBOT_API_KEY=local-dev-key
   BETTER_AUTH_URL=http://localhost:3000
   BETTER_AUTH_SECRET=local-dev-secret-please-ignore-0123456789abcdef
   GOOGLE_CLIENT_ID=...          # a Google OAuth client (see hackbot-ui README)
   GOOGLE_CLIENT_SECRET=...
   ```

3. **Run the UI**:

   ```bash
   cd services/hackbot-ui && npm install && npm run dev
   ```

   Open http://localhost:3000 and sign in with a `@mozilla.com` Google account. The stub then
   backs every run the UI reads, so the whole "Recent runs" surface — filters, paging, run
   detail — works against seeded data.

Note that the stub is read-only: triggering a run from the form will fail, since it implements
only the `GET` endpoints the dashboard reads.

Stop both processes with Ctrl+C in the terminals running them (avoid `pkill -f "next dev"`,
which would also kill unrelated Next.js dev servers).

---

## Run hackbot-api

The API connects to **Cloud SQL Postgres via the Google connector** (see
`app/database/connection.py`) and triggers **Cloud Run Jobs** + signs **GCS** URLs, so a
faithful run needs GCP application-default credentials and this config (env or a `.env` in
`services/hackbot-api/`):

- `GCP_PROJECT`, `GCP_REGION`, `RESULTS_BUCKET`
- `CLOUD_SQL_INSTANCE`, `DB_USER`, `DB_PASS`, `DB_NAME`
- `EXTERNAL_API_KEY` (the `X-API-Key` the UI must match)
- `PHABRICATOR_API_KEY` (32+ chars) and `WEBHOOK_SECRET` — **required at import** (settings
  validation fails without them, even for unrelated commands/tests). For local non-webhook
  use, dummies are fine (see `tests/conftest.py`).

Apply DB migrations first (manual — nothing auto-runs them):

```bash
cd services/hackbot-api
uv run alembic upgrade head          # against the configured Cloud SQL instance
```

Run the server:

```bash
cd services/hackbot-api
uv run --package hackbot-api uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Tests (no GCP needed; conftest stubs the required secrets):

```bash
cd services/hackbot-api && uv run --extra dev pytest -q
```

### Validating a migration without touching the real DB

`alembic` online mode uses the Cloud SQL connector, so to test a migration against a local
throwaway Postgres, generate the offline SQL and apply it:

```bash
cd services/hackbot-api
docker run -d --name hb-mig -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=hackbot -p 55432:5432 postgres:16
# temp ini with an absolute script_location + a sqlalchemy.url:
sed -e "s#script_location = %(here)s/alembic#script_location = $PWD/alembic#" \
    -e '/^prepend_sys_path/a sqlalchemy.url = postgresql://postgres:pw@127.0.0.1:55432/hackbot' \
    alembic.ini > /tmp/alembic_local.ini
export PHABRICATOR_API_KEY="api-$(printf 'a%.0s' {1..28})" WEBHOOK_SECRET=x
uv run --package hackbot-api alembic -c /tmp/alembic_local.ini upgrade head --sql > /tmp/up.sql
docker exec -i hb-mig psql -U postgres -d hackbot < /tmp/up.sql
docker rm -f hb-mig
```

---

## Run hackbot-ui

Full instructions: `services/hackbot-ui/README.md`. Summary — the real UI requires **Google
OAuth** (@mozilla.com only) and a reachable hackbot-api:

```bash
cd services/hackbot-ui
cp .env.example .env.local
# fill HACKBOT_API_URL, HACKBOT_API_KEY (== api's EXTERNAL_API_KEY),
#      BETTER_AUTH_SECRET (openssl rand -base64 32), GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
# Google OAuth authorized redirect URI: http://localhost:3000/api/auth/callback/google
npm install && npm run dev          # http://localhost:3000 -> /login
```

Auth is stateless (no DB); only `BETTER_AUTH_SECRET` must be shared across instances. There is
no auth bypass, and adding one isn't worth the risk — register a throwaway OAuth client for
local work. If you only need to look at the dashboard, the
[Quick local UI demo](#quick-local-ui-demo) avoids needing a real API.

---

## Run the pulse listener

Full instructions: `services/hackbot-pulse-listener/README.md`. Needs Pulse credentials and
points at a running hackbot-api:

```bash
export PULSE_USER=... PULSE_PASSWORD=...          # pulseguardian.mozilla.org
export DRY_RUN=true                               # log intended calls, don't POST
uv run --package hackbot-pulse-listener python -m app
```
