# HTTP Service Local Development

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
- `PHABRICATOR_API_KEY`: needed by Phabricator-backed endpoints.
- `PHABRICATOR_URL`: optional; defaults to Mozilla production Phabricator.
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
PHABRICATOR_API_KEY=
PHABRICATOR_URL=https://phabricator.services.mozilla.com
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

## Performance Regression Predictor

The Performance Regression Predictor uses the same HTTP service and background
worker, but it needs two extra local-development pieces while the model artifact
is unpublished:

- a local Hugging Face checkpoint mounted at the standard model directory;
- a Phabricator Conduit token so the worker can fetch diff metadata and raw
  diffs.

### Create The Secret File

Create `.env` in this directory (`http_service/.env` from the repository root)
with your Conduit token:

```dotenv
CONDUIT_API_TOKEN=api-replace-with-your-token
PHABRICATOR_URL=https://phabricator.services.mozilla.com
```

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
      PHABRICATOR_API_KEY: ${CONDUIT_API_TOKEN}
      PHABRICATOR_URL: "${PHABRICATOR_URL:-https://phabricator.services.mozilla.com}"
    volumes:
      - /absolute/path/to/predictor_model:/code/performanceregressionpredictormodel:ro
```

The host directory can be anywhere. Inside the container it must be mounted at:

```text
/code/performanceregressionpredictormodel
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
  test -f /code/performanceregressionpredictormodel/config.json \
  && echo "Model is mounted"
```

Request a prediction with an immutable Phabricator diff ID:

```sh
curl --compressed -sS \
  -w '\nHTTP status: %{http_code}\n' \
  -H "X-Api-Key: local-test" \
  http://localhost:8000/performanceregressionpredictor/predict/phabricator/DIFF_ID
```

The first request normally returns HTTP 202. Repeat the same request until it
returns HTTP 200.

For direct inference without Docker, Redis, Phabricator, or the HTTP API, see
the [Performance Regression Predictor CLI documentation](../docs/models/performance-regression-predictor.md#local-inference-with-the-cli).
