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

## Test on staging (Phabricator dev)

Use this procedure for an end-to-end test, including Phabricator submission,
Cloud Tasks retries, context retrieval, and publication of the AI review.
Uploading a diff or receiving “AI review requested” alone does not establish
that the worker processed it successfully.

### 1. Verify the deployment and worker configuration

Staging uses these resources:

| Resource | Staging value |
| --- | --- |
| Deployment branch | `deploy/reviewhelper-api-dev` in `mozilla/bugbug` |
| GCP project / region | `moz-bugbug` / `us-central1` |
| Cloud Run service | `reviewhelper-api-dev` |
| Cloud Tasks queue | `reviewhelper-dev-request-processing` |
| Phabricator | <https://phabricator-dev.allizom.org> |
| Bugzilla | <https://bugzilla-dev.allizom.org> |
| Phabricator repository / callsign | `ff-test-autoland-dev` / `FFTESTAUTOLANDDEV` |

Wait for the deployment build to succeed, then verify the serving revision
and its commit label rather than assuming a successful push is deployed:

```sh
gcloud auth login
gcloud run services describe reviewhelper-api-dev \
  --project=moz-bugbug --region=us-central1 \
  --format='yaml(metadata.labels.commit-sha,status.latestReadyRevisionName,status.traffic)'
```

The worker must have `PHABRICATOR_URL=https://phabricator-dev.allizom.org`,
`BUGZILLA_URL=https://bugzilla-dev.allizom.org`, credentials for those dev
instances, and `GITHUB_TOKEN` in its process environment. Configure the token
before deploying. Phabricator dev must point its Review Helper integration at
the dev service. A healthy `/health` response only verifies service health,
not these integration settings.

### 2. Prepare a small Firefox stack in the existing checkout

Use two related, nonempty commits. No clone or additional checkout is needed.
Keep their original commit subjects, including bug numbers; append the intended
`r?username` rather than replacing the subjects with test descriptions. Choose
a reviewer account that exists on Phabricator **dev** and differs from the
revision author: Phabricator rejects self-review, including `r?padenot` when
authenticated as `padenot`.

The bug must exist and be accessible on **Bugzilla dev**. A production bug
number in a commit subject does not guarantee this. `--no-bug` permits commits
without a bug; it does not bypass validation of a bug number already present.
Choose a stack with usable dev bugs, or explicitly arrange the dev bug linkage
before requesting a review.

moz-phab amends submitted commits with revision URLs. To preserve the original
stack in a jj checkout, duplicate only the chosen commits with `jj duplicate`
and submit those change IDs. This shares repository objects and uses the same
working directory; it does not copy the Firefox tree. Check `jj status` before
and after submission.

### 3. Submit explicitly to dev, for review

For a jj checkout, `.jj/.arcconfig` overrides the tracked `.arcconfig`.
The following runs from the Firefox root, refuses to overwrite an existing
override, and removes its temporary override even if submission fails. Replace
`FIRST_CHANGE` and `LAST_CHANGE` with the exact inclusive jj change IDs, whose
subjects already contain the intended `r?username`.

```sh
(
  set -eu
  test -d .jj
  test ! -e .jj/.arcconfig
  trap 'rm -f .jj/.arcconfig' EXIT
  cat > .jj/.arcconfig <<'JSON'
{
  "phabricator.uri": "https://phabricator-dev.allizom.org/",
  "repository.callsign": "FFTESTAUTOLANDDEV",
  "history.immutable": false
}
JSON
  moz-phab submit --no-bug --no-wip --yes FIRST_CHANGE LAST_CHANGE
)
```

The dev API token belongs to `https://phabricator-dev.allizom.org/api/` in
`~/.arcrc`; production credentials do not substitute for it. If needed, run
`moz-phab install-certificate` while the dev override is active.

Verify both returned URLs use the dev host, both revisions are **Needs Review**
(not WIP / Changes Planned), the reviewer is assigned, the repository is
`ff-test-autoland-dev`, and the second revision depends on the first. Repository
association and Phabricator project tags are separate fields. Do not invent a
project tag to substitute for the repository or bug linkage.

### 4. Verify public visibility, then request AI review

Check each revision's view policy, not just whether you can open it while
logged in. The worker requires `fields.policy.view == "public"` from
`differential.revision.search`. Phab-bot may take time to finalize visibility
after submission. A `secure-revision` tag or custom policy is a reason to
investigate the dev bug linkage and phab-bot processing; changing WIP status
or adding a reviewer does not itself make the revision public. Do not remove
a security restriction merely to get a test to run.

To check the policy, open the dev [Conduit API explorer](https://phabricator-dev.allizom.org/conduit/method/differential.revision.search/),
set `constraints` to `{"ids": [4258, 4259]}` (using your revision IDs), and run
the query. Inspect `result.data[].fields.policy.view` for each revision. This
also exposes its current `diffID`, `status`, and `repositoryPHID`.

Once both revisions are public, use the AI-review action on each dev revision.
Alternatively, rerun the submission command above with `--ai` added (keeping
`--no-wip` and the same dev override). `--ai` requests a review for every
revision in the selected stack, including updates. Record each revision ID
and **diff ID** shown by Phabricator; they are different identifiers.

Success means Review Helper publishes a review on the intended diff of each
revision. “Automated review completed with no comments” is also a successful
review. For context changes, inspect the review's context/tool details to
confirm it read the intended stack and file view.

### 5. Diagnose retries in Cloud Logging

Start with a revision ID, for example `D4258`:

```sh
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="reviewhelper-api-dev" AND "4258"' \
  --project=moz-bugbug --freshness=2d --limit=50 \
  --format='table(timestamp,resource.labels.revision_name,textPayload)'
```

Read the resulting **review request ID** (the database ID, not the revision
or diff ID), then include its processing URL to see HTTP responses and correlate
worker errors. For request `52`:

```sh
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="reviewhelper-api-dev" AND ("review request 52" OR httpRequest.requestUrl:"/internal/process/52")' \
  --project=moz-bugbug --freshness=2d --limit=100 \
  --format='table(timestamp,httpRequest.status,textPayload)'
```

“Encountered an issue and will be scheduled for retry” means `retry_pending`;
it does not identify the underlying error. For D4258 / Diff 6683 on
2026-10-15, request 52 repeatedly logged “Revision D4258 is not public” and
returned HTTP 409 on deployed commit `53bb44d1`. The worker had not reached
model execution. That is evidence of the visibility retry path, not a
successful context-retrieval test.

During the first 30 minutes after the **review request** was created, restricted
or inaccessible revisions return 409 so Cloud Tasks retries the same request
with its configured backoff. Public visibility lets a subsequent attempt
proceed. On the first attempt at or after the deadline, a still-restricted
revision becomes permanently failed and returns 204 to stop retrying. The
deadline does not schedule an attempt at exactly 30 minutes; inspect the queue
if retries appear delayed:

```sh
gcloud tasks queues describe reviewhelper-dev-request-processing \
  --project=moz-bugbug --location=us-central1 \
  --format='yaml(state,retryConfig)'
```

Other worker exceptions can also produce retries; read their traceback rather
than assuming every retry is a visibility delay. Re-requesting the same or an
older diff returns the existing status and does **not** reset a failed row or
queue a fresh run. To retest after terminal failure, fix the cause, upload a
genuinely updated diff, and request review for its new diff ID. Preserve failed
rows so failure statistics and request history remain intact.

## GitHub review context

Set `GITHUB_TOKEN` in the worker process environment to a GitHub personal access
token for authenticated base-commit checks and landing-merge searches. The
platform tools read it directly from the environment, like `PHABRICATOR_API_KEY`.
A local `.env` must be exported into the process environment for these tools.
Configure the token on both development and production workers.

`expand_context(before_patch=True)` reads the pre-patch file, including deleted
lines and files. Its default view includes the current patch.

Changed files come from Phabricator's latest changeset in the stack; untouched
files come from raw.githubusercontent.com at the stack's base commit. Those raw
file requests do not use the GitHub API token. HTTP errors, including rate limits,
are reported as context-retrieval warnings; they never trigger a read of the
latest branch contents. App authentication can replace the PAT later if needed.
