# Review Helper Backend

FastAPI backend for Mozilla's Review Helper supporting both Phabricator and GitHub platforms.

## Setup

1. Install dependencies:

   ```bash
   uv sync
   ```

2. Copy environment variables:

   ```bash
   cp .env.example .env
   ```

3. Run database migrations:

   ```bash
   uv run alembic upgrade head
   ```

4. Start the server:
   ```bash
   uv run uvicorn app.main:app --reload
   ```

## API Documentation

Once the server is running, visit `/docs` for interactive API documentation.

## Cloud SQL Setup

This application supports Google Cloud SQL for PostgreSQL with two connection methods:

### Local Development with Cloud SQL Auth Proxy

1. Install and run the Cloud SQL Auth Proxy:

   Follow instructions from the [Cloud SQL Auth Proxy documentation](https://docs.cloud.google.com/sql/docs/postgres/connect-auth-proxy#install).

2. Configure your `.env`:

   ```bash
   DATABASE_URL=postgresql+asyncpg://user:password@127.0.0.1:5432/reviewhelper
   ```

### Cloud Run Deployment

Cloud Run connects to Cloud SQL via Unix socket automatically. Set these environment variables:

```bash
CLOUD_SQL_INSTANCE=project-id:us-central1:instance-name
DB_USER=reviewhelper
DB_PASS=your-db-password
DB_NAME=reviewhelper
```

Ensure your Cloud Run service has the Cloud SQL connection configured in the deployment settings.

Pushing to the `deploy/reviewhelper-api-dev` branch deploys to the dev environment.

## GitHub review context

Set `GITHUB_TOKEN` in the worker process environment to a GitHub personal access
token for authenticated base-commit checks and landing-merge searches. The
platform tools read it directly from the environment, like `PHABRICATOR_API_KEY`.
A local `.env` must be exported into the process environment for these tools.
Configure the token on both development and production workers.

## GitHub App

### Design and current refactor boundaries

The integration separates GitHub access, agent execution, and service lifecycle:

```mermaid
flowchart TD
    GitHub[GitHub App webhooks] --> Requests[Request validation and enqueue]
    BackOffice[Back-office requests] --> Requests
    Allowlist[GITHUB_REPOSITORIES setting] --> Requests
    Requests --> Database[(Review database)]
    Requests --> Queue[Cloud Tasks]
    Queue --> Worker[Review worker]
    Worker --> Snapshot[GitHubPatch: immutable PR snapshot]
    Worker --> Agent[Existing review agent]
    Agent --> Wrappers[LangChain context tool wrappers]
    Wrappers --> Context[GitHubCodeContext]
    Context --> Snapshot
    Worker --> Publisher[GitHubReviewPublisher]
    Snapshot --> Transport[Injected GitHubAPI transport]
    Publisher --> Transport
    Transport --> Auth[Service-owned App authentication]
```

The reusable code is in `bugbug/tools/core/`:

- `platforms/base.py`: the existing `Patch` interface for diffs, metadata, and
  before/after files. `GitHubPatch` implements it alongside `PhabricatorPatch`.
- `platforms/github.py`: `GitHubPatch` fetches the whole PR and ordered commits;
  `GitHubCodeContext` provides revision-aware reads and remote search.
- `code_context.py`: a `CodeContext` protocol with a capability set and a
  human-readable description of provider limitations.
- `platforms/github_review.py`: `GitHubReviewPublisher` publishes a review and
  reconciles an uncertain write using a caller-supplied marker and bot identity.
  Its eligibility callback lets the service enforce enrollment and snapshot
  policy without putting service policy into the publisher.

These modules have no LangChain, FastAPI, SQLAlchemy, or service-settings imports.
They receive a `GitHubAPI` object exposing an async HTTP `request` method; a
different runtime can supply its own transport and credentials. The existing
`Patch` implementation uses `unidiff`, and the GitHub transport contract uses
`httpx.Response`.

Service-specific work stays under `app/`: App token and installation lookup, the
repository allowlist,
webhook authentication, request deduplication, queueing, eligibility checks, and
saving generated comments/platform IDs. The code-review agent receives a context
provider and only the LangChain tools corresponding to its capabilities.

The issue's broader refactor is **partially complete**:

| Boundary            | Implemented                                                                   | Remaining work                                                                                            |
| ------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Fetch a diff        | Existing `Patch` interface now has a GitHub implementation.                   | Move prompt formatting of ordered commits out of `GitHubPatch.commit_context` into the agent layer.       |
| Fetch code context  | GitHub has a framework-neutral provider and capability-selected wrappers.     | Adapt Searchfox to the same provider boundary; its current implementation still lives in LangChain tools. |
| Publish a review    | GitHub publication is a standalone implementation with injected dependencies. | Extract Phabricator publication from the service and define a shared publisher/finding interface.         |
| Declare agent tools | LangChain wrappers call the GitHub provider.                                  | Integrate with `libs/agent-tools`' existing framework-neutral registry and adapters.                      |

### Reuse in Hackbot

Hackbot can reuse the GitHub snapshot, context provider, and publisher without
the review-service database or LangChain. However, they are not yet registered
Hackbot tools. `libs/agent-tools/agent_tools/registry.py` expects plain async
handlers with an explicit first context parameter, and errors expressed through
`ToolError`. The current LangChain wrappers obtain context from `get_runtime`
and return warning strings instead.

A port would provide thin `agent_tools.registry.tool` handlers around the
provider, inject an authenticated transport through Hackbot's broker, and map
provider failures to `ToolError`. Publication belongs in Hackbot's recorded
action/apply lifecycle, rather than a tool that writes to GitHub during the agent
run. A future checkout-based provider can replace remote context behind the same
operations; richer capabilities need additional interfaces and tool declarations.

For a lightweight agent image, move the reusable implementation into a shared
library first: importing these modules is lightweight, but installing the
`bugbug` package currently brings its full dependency set. The publication
boundary also still uses GitHub-shaped dictionaries, and capabilities are string
names. Typed findings/results and an explicit capability vocabulary would make
the boundary clearer before a broader migration.

### Review behavior and setup

The service reviews an enrolled PR as a whole, with its ordered commits supplied
as distinct units. Findings must apply to the final PR diff and are published
together as a single `COMMENT` review, pinned to the reviewed head SHA.

Register a GitHub App that can be installed on any account to support both
organization repositories and personal repositories such as
`padenot/searchfox-cli`. Configure:

- **Contents: read**, **Issues: read**, and **Pull requests: read/write** repository
  permissions. Issues access is required for the `issue_comment` webhook used by
  `/review`; see [GitHub's event documentation](https://docs.github.com/en/webhooks/webhook-events-and-payloads#issue_comment).
- A webhook URL of `https://<service>/github/webhook` and a webhook secret.
- Events: `pull_request` and `issue_comment` (`installation` lifecycle events are
  supplied by GitHub).

Set `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (PEM; literal `\n` escapes are also
accepted), and `GITHUB_WEBHOOK_SECRET` in the service deployment. Installation
tokens are generated and renewed by the service. Developer personal tokens and
per-repository Actions workflows are unnecessary. `GITHUB_REVIEW_DELAY_SECONDS`
defaults to 30; it gives rapid pushes time to settle before processing. Obsolete
snapshots are discarded at processing and publication time.

Enrollment is configuration: a repository is reviewed only if the App is
installed on it and it is listed in `GITHUB_REPOSITORIES`, a JSON list that
defaults to `["padenot/searchfox-cli"]` for the pilot. `GITHUB_AUTOMATIC_REVIEWS` and
`GITHUB_ALLOW_AUTHOR_REQUESTS` (both default `true`) apply to every enrolled
repository. The integration uses the existing GitHub columns of `review_requests`
and needs no database migration.

### First trial on searchfox-cli

1. Deploy this backend revision and its locked dependencies to staging. Verify the
   existing model credentials and Cloud Tasks queue work, and that `WORKER_URL`
   points to staging. The review worker can run for up to
   30 minutes; configure the server request timeout accordingly.
2. Register a separate staging GitHub App with the permissions and events above.
   For a pilot owned by `padenot`, "Only on this account" is sufficient; use
   "Any account" when extending enrollment to other owners. Configure the HTTPS
   staging URL ending in `/github/webhook`. GitHub must be able to reach this route
   without an interactive login or Cloud Run IAM token; the service verifies the
   webhook signature. Store the App ID, private key, and matching webhook secret
   in staging's secret configuration. The App ID is shown on the App settings
   page. Generate and download its PEM private key there, and generate a separate
   random webhook secret (for example, `openssl rand -hex 32`). Set that same
   webhook secret on GitHub and in `GITHUB_WEBHOOK_SECRET`; set the downloaded
   PEM as `GITHUB_APP_PRIVATE_KEY` and the App ID as `GITHUB_APP_ID`. Keep secrets
   out of repository files. See [GitHub's private key instructions](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps).
3. Install the App on **only** `padenot/searchfox-cli`. Check the App's recent
   webhook deliveries for a successful `ping` response.
4. `padenot/searchfox-cli` is enrolled by default. To limit pilot usage, optionally
   set `GITHUB_AUTOMATIC_REVIEWS=false` and `GITHUB_ALLOW_AUTHOR_REQUESTS=false`.

5. Open a small non-draft test PR and post `/review` in its conversation as a
   repository maintainer. Check the webhook delivery, queued request, worker logs,
   and resulting bot review. Start with maintainer-only requests to control pilot
   usage; author requests can be enabled later.
6. Repeat `/review` at the same revision and verify no duplicate review. Push a
   new commit, request again, and verify the new review uses the new head SHA.
   Include a two-commit example where a later commit fixes an earlier defect to
   assess whether findings apply to the final state.
7. Set `GITHUB_AUTOMATIC_REVIEWS=true` to test opening and updating a PR without a
   manual command. Existing PRs are not automatically backfilled. To stop the
   trial, remove the repository from `GITHUB_REPOSITORIES` or uninstall the App;
   unpublished work is then not posted.

Deployment settings inspected on 2026-10-07:

| Setting                     | Dev                                                             | Prod                                                             |
| --------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------- |
| Cloud Run service           | `reviewhelper-api-dev`                                          | `reviewhelper-api-prod`                                          |
| Database                    | `reviewhelper-native-dev`                                       | `reviewhelper-native-prod`                                       |
| DB credential secret prefix | `reviewhelper-dev-db-`                                          | `reviewhelper-prod-db-`                                          |
| Task queue                  | `reviewhelper-dev-request-processing`                           | `reviewhelper-prod-request-processing`                           |
| Worker URL                  | `https://reviewhelper-api-dev-510094885435.us-central1.run.app` | `https://reviewhelper-api-prod-510094885435.us-central1.run.app` |

Both databases exist on the same Cloud SQL instance,
`moz-bugbug:us-central1:review-helper`. Both services use the same runtime service
account and model/Qdrant credential references, so this is not full infrastructure
or credential isolation. Database load and shared API quotas can still affect
production. Both queues have no target override, and dev allows unauthenticated
Cloud Run invocation for signed GitHub webhooks. Recheck these settings before
deployment.

No repository workflow or mandatory `review-context.toml` is required. This trial
must still validate App-authenticated code search, model execution, and review
publication; read-only GitHub smoke tests and mocked
tests do not cover that end-to-end path.

If a command produces no review, inspect the App's webhook deliveries first:
a successful delivery returns HTTP 204. HTTP 401/403 can indicate a front-end
authentication gate or an incorrect signature secret; compare the response with
the service logs. If delivery succeeds, inspect Cloud Tasks and worker logs for
the request. Confirm the repository is in `GITHUB_REPOSITORIES`, the PR is open
and non-draft, and the comment is exactly `/review` from an allowed user.
Repeating a request for an already published snapshot deliberately returns the
existing review. Superseded snapshots are recorded as failed, with an error
starting with `Superseded:`. A failed snapshot cannot currently be reset through
the request API; fix the failure and use a new test revision. Failed webhook deliveries can be redelivered from the
App settings page.

### Back-office contract

Back-office calls use the existing `Authorization: Bearer <EXTERNAL_API_KEY>`
authentication. Keep this service key on the back-office server. Back-office
managed enrollment is not implemented yet; repositories are enrolled through
`GITHUB_REPOSITORIES`.

With `GITHUB_AUTOMATIC_REVIEWS=true`, open non-draft PRs are reviewed on `opened`,
`synchronize`, `reopened`, and `ready_for_review`. Each new head revision gets a
new review. Otherwise, each revision requires an explicit request. Changing policy
does not retroactively enqueue existing PRs or cancel an already requested review;
removing a repository from `GITHUB_REPOSITORIES` cancels unpublished work.

In either mode, a `/review` comment in the PR conversation requests a review.
Repository writers/maintainers/admins may request reviews. PR authors may also
request them when `GITHUB_ALLOW_AUTHOR_REQUESTS` is true (the default); other users
and bots cannot. Draft and closed PRs are not reviewed. Commands on issues or
inline review comments are ignored.

`POST /request/github` requests or checks a review independently of webhooks:

```json
{
  "owner": "padenot",
  "repo": "searchfox-cli",
  "pr_number": 15,
  "user_id": 270485,
  "user_name": "padenot"
}
```

The caller authenticates and authorizes the requesting developer. The service
resolves the current PR head and the App installation; callers cannot supply an
arbitrary SHA or installation. Responses contain `id`, `status`, and `sha`.
Requests for the same PR/head snapshot are idempotent, including requests after
successful publication. The existing Phabricator `POST /request` is unchanged.

### Code context and limits

No checkout is required. File expansion reads the exact PR head, including forks.
Search scans changed files at that revision and uses GitHub's default-branch code
index to discover other files, which are then read at the PR head. Search and file
contents are cached within a review. Only text search, textual identifier lookup,
and file expansion are exposed; semantic definition/call-graph/layout tools are
unavailable. Regex and language/test filters are not supported by this provider.
GitHub code search has a separate quota (normally 10 requests/minute), independent
of the ordinary API limit. Failed discovery falls back to searching changed files.

Optional `review-context.toml` is fetched from the pinned **target branch** revision
using App authentication. Referenced instruction files default to that same revision;
PR changes to these files do not change the instructions for their own review.
Its existing rules can load project-specific review guidance. Without configuration,
the generic prompt uses the repository name rather than assuming Firefox.

Reviews exceeding the existing token budget, 250 KB of whole-PR diff, or 500 KB of
combined PR/commit diffs are rejected rather than silently truncated. The complete
commit list is required. GitHub feedback ingestion and automatic redelivery of
failed GitHub webhook deliveries are not implemented here; failed deliveries can
be redelivered from GitHub, and a pending request can be retried through the API.
The back-office UI and App registration/deployment are separate from this backend.
