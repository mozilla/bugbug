# Hackbot Launchpad

A small Next.js web UI for **demonstrating** the [`hackbot-api`](../hackbot-api).
It lets you:

- **Trigger** the `bug-fix` agent by entering a Bugzilla bug ID (plus optional
  model / max-turns / effort).
- **Observe state** — the dashboard and the run detail page poll the API and
  show each run's status live (`pending → running → succeeded / failed / timed_out`).
- **Read the result** on completion — the agent's summary findings (rendered as
  an "Agent log" pane when a free-text log/output field is present) and the
  artifacts written to the results bucket, each a **download link** (the browser
  is redirected to a short-lived signed GCS URL).

> This is a demo surface, not a system of record. The recent-runs panel is
> sourced entirely from the upstream `GET /runs` endpoint and filtered to the
> agent selected in the trigger form (carried in the `?agent=` URL param).

## Architecture

```
Browser ──> Next.js route handlers (/api/*) ──> hackbot-api (X-API-Key)
            └─ better-auth (Google, @mozilla.com only)
```

The `hackbot-api` key never reaches the browser: every call goes through a
server-side route handler in `app/api/*` that injects the `X-API-Key` header
(see `lib/hackbot.ts`). Those handlers also re-validate the session.

### Authentication

Sign-in is **Google OAuth via [better-auth](https://better-auth.com)**, limited
to `@mozilla.com` accounts:

- **No database — fully stateless.** `lib/auth.ts` configures better-auth with
  no `database`, so the session lives entirely in a signed + encrypted (JWE)
  cookie and the server never queries any store to validate it. The only shared
  state is `BETTER_AUTH_SECRET`. See better-auth's
  [stateless session docs](https://better-auth.com/docs/concepts/session-management#basic-stateless-setup).
- The `@mozilla.com` restriction is enforced in two mode-independent layers:
  the Google provider's `mapProfileToUser` rejects non-Mozilla identities during
  the OAuth callback (before a session is issued), and `getAuthedEmail()`
  (`lib/session.ts`) re-checks the domain on every proxy request. `hd: "mozilla.com"`
  is also passed to Google as a UI hint.
- `middleware.ts` redirects unauthenticated visitors to `/login` (and returns
  `401` JSON for `/api/*`).

## Endpoints used (hackbot-api)

| UI action         | hackbot-api call                        |
| ----------------- | --------------------------------------- |
| Trigger a run     | `POST /agents/bug-fix/runs`             |
| Poll run state    | `GET /runs/{run_id}`                    |
| Download artifact | `GET /runs/{run_id}/artifacts/{path}` † |
| (available)       | `GET /agents`                           |

## Local development

1. Install dependencies:

   ```bash
   npm install
   ```

2. Configure the environment:

   ```bash
   cp .env.example .env.local
   # fill in HACKBOT_API_URL, HACKBOT_API_KEY,
   # BETTER_AUTH_SECRET, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
   ```

   For the Google OAuth client, add this authorized redirect URI:
   `http://localhost:3000/api/auth/callback/google`

   (No database setup or migration step — auth is stateless.)

3. Run it:

   ```bash
   npm run dev
   ```

   Open http://localhost:3000 — you'll be redirected to `/login`.

## Production build / container

```bash
docker build -t hackbot-ui -f services/hackbot-ui/Dockerfile services/hackbot-ui
docker run -p 3000:3000 --env-file services/hackbot-ui/.env.local hackbot-ui
```

The image uses Next.js `output: "standalone"`.

### Cloud Run

Stateless auth makes this Cloud Run-friendly out of the box: sessions are
self-contained cookies, so they survive scale-to-zero and work across any number
of instances — **as long as every instance shares the same `BETTER_AUTH_SECRET`**
(set it as a secret/env var on the service). No database to provision.

Use `./deploy.sh` (build → push to Artifact Registry → `gcloud run deploy`). It
keeps secrets in Secret Manager and reads everything else from env vars. The
service runs as a **dedicated least-privilege service account**
(`hackbot-ui-run@<project>`) that the script creates and grants
`secretmanager.secretAccessor` on just the three secrets it reads — no other GCP
permissions (it reaches hackbot-api over HTTPS with the API key, not via IAM).

The hackbot-api key reuses the existing shared **`external-api-key`** secret
(override with `API_KEY_SECRET=...`), so only two UI-specific secrets need
creating: `hackbot-ui-auth-secret` and `hackbot-ui-google-secret`.

```bash
# one-time: enable APIs + create the two UI secrets (see the header of deploy.sh).
# The deployer needs run.admin + iam.serviceAccountUser on the new SA.

# first deploy (no BETTER_AUTH_URL yet — Cloud Run assigns the URL):
PROJECT=my-proj REGION=us-central1 \
HACKBOT_API_URL=https://hackbot-api-xxxx.run.app \
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com \
./deploy.sh

# then: add <printed-url>/api/auth/callback/google to the Google OAuth client,
# and re-run with BETTER_AUTH_URL=<printed-url> to finalize.
```

`HACKBOT_API_URL` is the hackbot-api's public Cloud Run URL; `HACKBOT_API_KEY`
must match its `X-API-Key`.

## Environment variables

| Variable               | Purpose                                            |
| ---------------------- | -------------------------------------------------- |
| `HACKBOT_API_URL`      | Base URL of hackbot-api (no trailing slash)        |
| `HACKBOT_API_KEY`      | Value for the `X-API-Key` header (server-side)     |
| `BETTER_AUTH_URL`      | Public base URL of this app                        |
| `BETTER_AUTH_SECRET`   | Session signing secret (`openssl rand -base64 32`) |
| `GOOGLE_CLIENT_ID`     | Google OAuth client ID                             |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret                         |
