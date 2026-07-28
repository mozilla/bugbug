# Hackbot Pulse Listener

Its job is to **subscribe** to Taskcluster failure messages, **filter** them down to
failures worth acting on, **dedupe** them, and dispatch a hackbot agent through the
hackbot-api. It deliberately holds no investigation logic: each agent resolves the push
and commits itself, so the listener only decides _what to hand off_. When a run finishes
(minutes later) the listener polls the result and emails a report.

Failed **build** tasks go to `build-repair`; failed **test** tasks go to `test-repair`.

## How it works

1. **Subscribe.** Consume `task-failed` messages from `pulse.mozilla.org`.
2. **Filter** to a watched `project` (`WATCHED_REPOS`, default `autoland`), then by task
   kind: build tasks (compile/link errors) take the build-repair path; test tasks take the
   test-repair path. Fetch the task definition for `GECKO_HEAD_REV` (not in the message),
   and for a test task the failing groups (mozci).
3. **Dedupe** with in-memory TTL caches: build-repair once per revision; test-repair once per
   `(push, test group)`, so a manifest failing across chunks is investigated once. Groups are
   claimed before the checks below, so sibling chunks never repeat them.
4. **Judge** whether the failure is worth a run. Both paths fail open — a mozci or
   Taskcluster error runs the agent rather than dropping a possible regression.
   - _Build:_ keep only failures this push introduced (not inherited from an ancestor).
   - _Test:_ first drop whatever Treeherder has already judged not to be a new
     regression (intermittent, infra, expected-fail, fixed-by-commit); Treeherder
     ingests a minute or so behind us, so the gate waits for the job to appear. Then
     keep only groups that are new for this task's own configuration. One run per
     task, carrying only the task id.
5. **Dispatch & report.** `POST /agents/{agent}/runs`, poll `GET /runs/{run_id}` until
   terminal, then email a hackbot UI link, the analysis summary, a Treeherder link, and the
   commit the agent blamed. Build-repair mails the blamed commit's author; test-repair mails
   the notification address (`TEST_REPAIR_NOTIFICATION_EMAIL`).

The dedupe caches and pending-run tracking are in-memory (reset on restart).

## Run locally

```bash
export PULSE_USER=... PULSE_PASSWORD=...          # https://pulseguardian.mozilla.org
export HACKBOT_API_URL=https://hackbot-api.../ HACKBOT_API_KEY=...
export HACKBOT_UI_URL=https://hackbot-ui.../
export WATCHED_REPOS=autoland
export DRY_RUN=true                               # log intended calls, don't POST
uv run --package hackbot-pulse-listener python -m app
```

Email is sent only when `SENDGRID_API_KEY` and `NOTIFICATION_SENDER` are set; otherwise it
is logged and skipped. Build-repair mails the blamed commit's author (looked up in the
firefox GitHub mirror), the pushing developer, and the `NOTIFICATION_TEAM_EMAIL` team
address if set; test-repair mails `TEST_REPAIR_NOTIFICATION_EMAIL` (with the culprit author CC'd). Set
`NOTIFICATION_OVERRIDE_EMAIL` to route every notification to a single address (useful for
local testing). By default only build-repair runs that produced a patch are emailed; set
`NOTIFY_ONLY_WITH_PATCH=false` to also notify on transient / not-to-blame runs (test-repair always
notifies).
When `NOTIFICATION_TEAM_EMAIL` is set, notifications use it as `Reply-To` so recipients can
reply with feedback on the analysis.

## Test

```bash
uv run --package hackbot-pulse-listener pytest services/hackbot-pulse-listener/tests
```

## Deploy

Cloud Run worker pool (no HTTP). See `deploy.sh`.
