# Hackbot Pulse Listener

Listens to Taskcluster build-failure pulse messages, and for failed **Firefox build
tasks** triggers the `build-repair` hackbot agent through the hackbot-api. Notification
about the run outcome is handled separately (in the API or another service).

## How it works

1. Consume `task-failed` messages from `pulse.mozilla.org`.
2. Keep only **build-kind** tasks (`tags.kind == "build"`) on a watched `project`
   (`WATCHED_REPOS`, default `try`). Build tasks don't run tests, so a failure is a
   compilation/link error.
3. Fetch the task definition to read `GECKO_HEAD_REV` (the revision is not in the message).
4. Dedupe by revision with an in-memory TTL cache, so only one agent run is triggered per
   revision even when many build tasks fail for the same push.
5. `POST /agents/build-repair/runs`.

The dedupe cache is in-memory (reset on restart).

## Run locally

```bash
export PULSE_USER=... PULSE_PASSWORD=...          # https://pulseguardian.mozilla.org
export HACKBOT_API_URL=https://hackbot-api.../ HACKBOT_API_KEY=...
export WATCHED_REPOS=try
export DRY_RUN=true                               # log intended calls, don't POST
uv run --package hackbot-pulse-listener python -m app
```

## Test

```bash
uv run --package hackbot-pulse-listener pytest services/hackbot-pulse-listener/tests
```

## Deploy

Cloud Run worker pool (no HTTP). See `deploy.sh`.
