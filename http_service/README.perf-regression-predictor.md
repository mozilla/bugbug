# Perf Regression Predictor: HTTP Service Local Development

This describes the local Docker Compose setup used to exercise the Perf
Regression Predictor endpoint through the HTTP service and its background
worker.

Run Docker Compose commands from this directory, not the repository root. The
root may have a different Compose application with unrelated credentials.

```sh
cd /path/to/bugbug/http_service
```

## Services

The local Compose file defines:

- `redis`: local Redis used by the HTTP service and RQ worker.
- `bugbug-http-service`: Flask HTTP API on `http://localhost:8000`.
- `bugbug-http-service-bg-worker`: background worker that downloads/loads models
  and processes queued prediction jobs.
- `bugbug-http-service-rq-dasboard`: optional local RQ dashboard on
  `http://localhost:9181`.

## Environment

Most environment variables are optional for local startup, but individual
endpoints may need service-specific credentials:

- `BUGBUG_BUGZILLA_TOKEN`: needed by Bugzilla bug classification endpoints.
- `BUGBUG_GITHUB_TOKEN`: needed by GitHub issue classification endpoints.
- `BUGBUG_REPO_DIR`: local Mercurial clone used by push-based endpoints (test
  selection and the perf regression predictor); defaults to a temporary
  `bugbug-hg` directory.
- `BUGBUG_ALLOW_MISSING_MODELS=1`: useful for local development when you only
  need one model and do not have every model artifact locally.

The API checks for the presence of an `X-Api-Key` header. For local testing, the
value can be any non-empty string unless you are testing deployment-specific
authentication behavior.

If you need local secrets, create `.env` in this directory
(`http_service/.env` from the repository root):

```dotenv
BUGBUG_BUGZILLA_TOKEN=
BUGBUG_GITHUB_TOKEN=
BUGBUG_ALLOW_MISSING_MODELS=1
```

Protect the file:

```sh
chmod 600 .env
```

`.env` is ignored by Git. Do not put real tokens in tracked Compose files.

## Start The Service

Confirm that you are using the HTTP service Compose application:

```sh
docker compose config --services
```

Start the core services:

```sh
docker compose up --build \
  redis \
  bugbug-http-service \
  bugbug-http-service-bg-worker
```

The background worker downloads and validates model artifacts during startup
unless the image was built with `CHECK_MODELS=0`.

In another terminal, follow the worker logs:

```sh
docker compose logs -f bugbug-http-service-bg-worker
```

## Test A Generic Model Endpoint

Use an endpoint for a model that the HTTP service exposes through the generic
Bugzilla classifier route:

```sh
curl --compressed -sS \
  -w '\nHTTP status: %{http_code}\n' \
  -H "X-Api-Key: local-test" \
  http://localhost:8000/component/predict/123456
```

The first request normally queues the job and returns:

```text
{"ready":false}
HTTP status: 202
```

Repeat the same request after the worker finishes. A completed prediction
returns HTTP 200.

## Optional RQ Dashboard

Start the dashboard when you want to inspect queued, running, or failed jobs:

```sh
docker compose up bugbug-http-service-rq-dasboard
```

Open:

```text
http://localhost:9181
```

This is a local debugging interface and should not be exposed publicly without
authentication.

## Stop The Service

```sh
docker compose down
```

## Perf Regression Predictor Setup

The Perf Regression Predictor uses the same HTTP service and background
worker. While the model artifact is unpublished, the only extra
local-development piece it needs is a local Hugging Face checkpoint mounted at
the standard model directory.

The worker resolves each push `(branch, rev)` against its own local Mercurial
clone (`BUGBUG_REPO_DIR`, defaulting to a temporary `bugbug-hg` directory),
pulling the revision from `https://hg.mozilla.org/{branch}/` — the same
mechanism the `/push/.../schedules` test-selection endpoint uses. No Phabricator
credentials are required.

### Create The Compose Override

Create `http_service/docker-compose.override.yml` and replace the source side
of the volume with the absolute path to your local Hugging Face checkpoint:

```yaml
services:
  bugbug-http-service-bg-worker:
    build:
      args:
        CHECK_MODELS: "0"
    environment:
      BUGBUG_ALLOW_MISSING_MODELS: "1"
    volumes:
      - /absolute/path/to/predictor_model:/code/perfregressionpredictormodel:ro
```

The host directory can be anywhere. Inside the container it must be mounted at:

```text
/code/perfregressionpredictormodel
```

That is the same fixed model-directory convention used by the other HTTP worker
models. `CHECK_MODELS=0` skips startup artifact downloads while this model is
unpublished, and `BUGBUG_ALLOW_MISSING_MODELS=1` lets the worker start without
unrelated model artifacts.

Start the core services as usual:

```sh
docker compose up --build \
  redis \
  bugbug-http-service \
  bugbug-http-service-bg-worker
```

Verify that the checkpoint is mounted:

```sh
docker compose exec bugbug-http-service-bg-worker \
  test -f /code/perfregressionpredictormodel/config.json \
  && echo "Model is mounted"
```

Request a prediction for a push, identified by its branch and revision:

```sh
curl --compressed -sS \
  -w '\nHTTP status: %{http_code}\n' \
  -H "X-Api-Key: local-test" \
  http://localhost:8000/perfregressionpredictor/predict/push/autoland/REV
```

The first request normally returns HTTP 202. Repeat the same request until it
returns HTTP 200.

For direct inference without Docker, Redis, or the HTTP API, see
the [Perf Regression Predictor CLI documentation](../docs/models/perf-regression-predictor.md#local-inference-with-the-cli).
