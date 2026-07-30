# Hackbot Pulse Listener

Its job is to **subscribe** to Taskcluster failure messages, **filter** them down to
failures worth acting on, **dedupe** them, and dispatch a hackbot agent through the
hackbot-api. It deliberately holds no investigation logic: each agent resolves the push
and commits itself, so the listener only decides _what to hand off_. When a run finishes
(minutes later) the listener polls the result and emails a report.

Failed **build** tasks go to `build-repair`; failed **test** tasks go to `test-repair`.

## How it works

1. **Subscribe.** Consume `task-failed` messages from `pulse.mozilla.org`.
2. **Route** to a watched `project` (`WATCHED_REPOS`, default `autoland`), then by task
   kind: build tasks (`tags.label` contains `build` and not `test`, so a failure is a
   compilation/link error) take the build-repair path; test tasks take the test-repair
   path. Fetch the task definition for `GECKO_HEAD_REV` (not in the message).
3. **Discard what is not this push's failure**, on both paths:
   - Tasks scheduled by an **action task** rather than by the push: `extra.parent` points
     at the decision task (= the task group) for everything the push scheduled, and at the
     action-callback task for a backfill or retrigger.
   - Pushes that landed more than `MAX_PUSH_AGE_HOURS` ago (default 24). A failure can
     surface long after its push, and by then the push has been superseded.
4. **Dedupe** with in-memory TTL caches, both keyed by revision: one run per push per
   agent, triggered on the first failing task worth investigating. The test-repair
   agent works from a single task but reads the push's other failures itself, so a
   second run for the same push would re-tread the same ground. A revision is recorded
   only once a run is actually triggered, so a task rejected as intermittent or
   inherited leaves the push open for the next one.
5. **Judge** whether the failure is worth a run. Every check fails open — an upstream
   error runs the agent rather than dropping a possible regression.
   - _Build:_ keep only failures this push introduced, waiting for an unsettled ancestor
     build to finish first.
   - _Test:_ first drop whatever Treeherder judges not to be a new regression
     (intermittent, infra, expected-fail, fixed-by-commit). Treeherder ingests a minute or
     so behind us and classifies a few minutes after that, so this gate waits for the job
     to appear and then for its verdict — it is the cheap filter, and most test failures
     stop here, before any group is fetched or any ancestor walked. What survives is
     narrowed to the groups that are new for this task's own configuration (platform and
     build option), then Treeherder is asked once more, since a verdict can still land
     while that walk runs. The run carries only the task id.
6. **Budget.** At most `MAX_TEST_REPAIRS_PER_DAY` test-repair runs (default 100) may
   start in any rolling 24 hours. A slot is taken when a run is triggered and given
   back if the trigger fails, so only runs that really started count. Once the budget
   is spent, later test failures stop before any Treeherder work. Build-repair is not
   capped.
7. **Dispatch & report.** `POST /agents/{agent}/runs`, poll `GET /runs/{run_id}` until
   terminal, then email a hackbot UI link, the analysis summary, a Treeherder link, and the
   commit the agent blamed. Build-repair looks the blamed commit up in the firefox GitHub
   mirror and mails its author; test-repair mails the notification address
   (`TEST_REPAIR_NOTIFICATION_EMAIL`).

The dedupe caches, the daily budget and pending-run tracking are all in-memory, so
a restart resets them.

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
address if set; test-repair mails only `TEST_REPAIR_NOTIFICATION_EMAIL` and the team address --
never the culprit author or the pushing developer, though the culprit is still named
in the body. Set
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
