# hackbot-mcp

A remote [MCP](https://modelcontextprotocol.io) server for Hackbot. Mozilla
developers can use it to start Hackbot agent runs, check on them, read their
results and apply their actions from an MCP client such as Claude Code or
Codex. They sign in with their own Mozilla Google account.

It's a proxy, like [hackbot-ui](../hackbot-ui):

- Users sign in through MCP OAuth, with Google as the identity provider. Only
  verified `@mozilla.com` accounts get in: Google must report the account's
  Workspace domain (`hd`) as `mozilla.com`, mark the email as verified, and
  the email itself must be `@mozilla.com`.
- The server calls [hackbot-api](../hackbot-api) with its own credential and
  sends the signed-in user's email as `X-On-Behalf-Of`. That email only ever
  comes from the verified token, never from a tool argument.

## Tools

| Tool               | What it does                                                                             |
| ------------------ | ---------------------------------------------------------------------------------------- |
| `list_agents`      | The agents, with their descriptions and input schemas.                                   |
| `start_run`        | Starts a run and returns right away. Holds actions for review unless `auto_apply=true`.  |
| `get_run`          | A run's current state.                                                                   |
| `list_my_runs`     | The signed-in user's runs, newest first.                                                 |
| `get_result`       | A run's findings, artifacts and actions, with each action's state.                       |
| `get_artifact_url` | A short-lived download URL for an artifact, e.g. `changes/changes.patch` (for `git am`). |
| `apply_actions`    | Applies all of a run's pending actions. Only the run's requester can do this.            |

No tool waits for a run to finish: runs take up to an hour or more. The
server's MCP instructions tell the model to confirm with the user before
`start_run`, to poll with `get_run`, and to list every pending action before
`apply_actions`. Each tool's annotations (read-only, or destructive for
`apply_actions`) also decide how the MCP client asks for permission.

`start_run` doesn't accept `require_review` (`auto_apply` sets it) or
`bugzilla_needinfo_flag_id` (that input belongs to the Bugzilla webhook).

## Connecting

Claude Code:

```sh
claude mcp add --transport http hackbot https://<hackbot-mcp host>/mcp
```

Then run `/mcp` in Claude Code, pick `hackbot`, and sign in with your Mozilla
Google account in the browser window that opens.

Codex:

```sh
codex mcp add hackbot --url https://<hackbot-mcp host>/mcp
codex mcp login hackbot
```

## Configuration

Environment variables (pydantic-settings, see `src/hackbot_mcp/config.py`):

| Variable                                              | Secret    | Description                                                                                              |
| ----------------------------------------------------- | --------- | -------------------------------------------------------------------------------------------------------- |
| `BASE_URL`                                            |           | Public URL of this server, without `/mcp`. Must stay the same, because OAuth metadata and tokens use it. |
| `GOOGLE_CLIENT_ID`                                    |           | Google OAuth client (Web application) with redirect URI `<BASE_URL>/auth/callback`.                      |
| `GOOGLE_CLIENT_SECRET`                                | yes       | That client's secret.                                                                                    |
| `JWT_SIGNING_KEY`                                     | yes       | 32+ random characters. Signs the tokens issued to MCP clients. Rotating it signs everyone out.           |
| `STORAGE_ENCRYPTION_KEY`                              | yes       | 32+ random characters. Encrypts the stored OAuth state (users' Google tokens).                           |
| `CLOUD_SQL_INSTANCE`                                  |           | `project:region:instance` of the Postgres instance that holds the OAuth state.                           |
| `CLOUD_SQL_IP_TYPE`                                   |           | `PUBLIC` (default), `PRIVATE` or `PSC`.                                                                  |
| `DB_USER` / `DB_PASS` / `DB_NAME`                     | `DB_PASS` | Database credentials; `DB_NAME` defaults to `hackbot_mcp`. The table is created on first use.            |
| `DATABASE_URL`                                        | yes       | A plain `postgresql://` URL, instead of `CLOUD_SQL_INSTANCE` (local Postgres).                           |
| `HACKBOT_API_URL`                                     |           | hackbot-api's URL, which is also the audience of the ID token sent to it.                                |
| `HACKBOT_API_KEY`                                     | yes       | Optional. Without it, the server uses its service account's ID token (see below).                        |
| `HACKBOT_UI_URL`                                      |           | Base for the run links in tool results.                                                                  |
| `ENVIRONMENT`, `SENTRY_DSN`, `SENTRY_RELEASE`, `PORT` |           | As for hackbot-api.                                                                                      |

Outside `ENVIRONMENT=development`, one of `CLOUD_SQL_INSTANCE` and
`DATABASE_URL` is required. Cloud Run runs several instances, a client's
requests can land on any of them, and all of them need to see the same client
registrations and tokens.

### Authenticating to hackbot-api

By default the server sends a Google ID token for its own service account,
from the metadata server, with audience `HACKBOT_API_URL`. The service account
must be in hackbot-api's `ALLOWED_SERVICE_ACCOUNTS`, and hackbot-api records it
as each run's `requested_via`. Set `HACKBOT_API_KEY` to use the shared key
instead.

### Infrastructure (webservices-infra)

- A Google OAuth client in the hackbot project with redirect URI
  `<BASE_URL>/auth/callback`. An "Internal" consent screen also limits sign-in
  to the Mozilla Workspace.
- A Cloud Run service, `hackbot-mcp` (with `-dev` and `-stage` suffixes outside
  prod):
  - allows unauthenticated invocations, since the server authenticates users
    itself;
  - runs as its own service account, with `roles/cloudsql.client` and access
    to the secrets above;
  - has a stable public URL.
- A `hackbot_mcp` database and user on hackbot's Cloud SQL instance.
- The service account added to hackbot-api's `ALLOWED_SERVICE_ACCOUNTS`.
- The `dev-hackbot-mcp` and `stage-hackbot-mcp` Cloud Build triggers for
  `cloudbuild.yaml`, and a `hackbot-mcp` Sentry project.

## Releasing

As for hackbot-api: bump `VERSION` to release to stage. Push to
`dev/services/hackbot-mcp` to deploy to dev. Promote to prod with the
"hackbot prod deploy" workflow. See [cloudbuild.yaml](cloudbuild.yaml) and
[tools/hackbot-deploy](../../tools/hackbot-deploy).

## Running locally

Create a Google OAuth client (Web application) with the redirect URI
`http://localhost:8080/auth/callback`, then:

```sh
cat > services/hackbot-mcp/.env <<EOF
BASE_URL=http://localhost:8080
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
JWT_SIGNING_KEY=$(openssl rand -hex 32)
STORAGE_ENCRYPTION_KEY=$(openssl rand -hex 32)
HACKBOT_API_URL=https://<a dev hackbot-api>
HACKBOT_API_KEY=...
EOF
cd services/hackbot-mcp
uv run --package hackbot-mcp python -m hackbot_mcp.server
claude mcp add --transport http hackbot-local http://localhost:8080/mcp
```

Without `DATABASE_URL`, OAuth state is kept in memory, so restarting the
server signs everyone out.

Tests:

```sh
cd services/hackbot-mcp
uv run --locked --with pytest --with pytest-asyncio pytest tests/
```

The tests mock hackbot-api and Google with `httpx.MockTransport`.
