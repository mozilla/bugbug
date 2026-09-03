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
   - Pushes that landed more than `MAX_PUSH_AGE_HOURS` ago (default 6, comfortably more
     than a push needs to build and test). A failure can surface long after its push,
     and by then the push has been superseded and a sheriff has dealt with it.
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
   - _Test:_ first drop whatever a Treeherder classification says is not a new
     regression (intermittent, infra, expected-fail, fixed-by-commit). Treeherder ingests
     the job a minute or so behind us, and sheriffs classify it — mostly by hand — a
     median of ~1 minute past the end of the job, ~11 minutes at p90 (measured over 40
     recent autoland pushes), so this gate waits for the job to appear and then up to
     `TREEHERDER_CLASSIFICATION_WAIT_SECONDS` for its verdict. It is the cheap filter,
     and most test failures stop here, before any group is fetched or any ancestor
     walked. What survives goes through the ancestor walk below, then Treeherder is
     asked once more, since a verdict can still land while that walk runs. The run
     carries only the task id.
   - _Both:_ a walk is abandoned as soon as another task triggers the run for that
     push. One push can emit dozens of failing tasks, and without this each one holds
     a worker for the full wait only to find the push already handed off.
6. **Budget.** At most `MAX_TEST_REPAIRS_PER_DAY` test-repair runs (default 50) may
   start in any rolling 24 hours. A slot is taken when a run is triggered and given
   back if the trigger fails, so only runs that really started count. Once the budget
   is spent, later test failures stop before any Treeherder work. Build-repair is not
   capped.
7. **Dispatch & report.** `POST /agents/{agent}/runs`, poll `GET /runs/{run_id}` until
   terminal, then email a hackbot UI link, the analysis summary, a Treeherder link, and the
   commit the agent blamed. Build-repair looks the blamed commit up in the firefox GitHub
   mirror and mails its author; test-repair mails only the team address
   (`NOTIFICATION_TEAM_EMAIL`) -- sheriffs are notified by the agent in Slack instead.

The dedupe caches, the daily budget and pending-run tracking are all in-memory, so
a restart resets them.

## The ancestor walk

Both paths ask the same question — did this push introduce the failure, or inherit it
from an ancestor? — by walking the push's ancestors (mozci resolves the chain from the
pushlog) until one gives a verdict on the failing unit, and both wait up to ten minutes
for an ancestor still running before failing open. What differs is the unit compared:

- _Build:_ the task **label**. A build label carries no chunk number, so it means the
  same thing on every push: an ancestor whose same label passed makes the failure new,
  one where it already failed makes it inherited.
- _Test:_ each failing **manifest** (Treeherder's "group") separately, compared within
  this task's own **configuration** — platform plus build option — and not by label. A
  test label carries its chunk number and chunk assignments drift between pushes, so
  comparing labels would make ancestors look as though they never ran the manifest,
  whereas a manifest only appears on the tasks that actually ran it. Consulting one
  configuration keeps a manifest already broken on another platform from masking a
  genuine new failure here. Only the manifests that come back new are kept, and the task
  is skipped if none do.
- _Test with no manifests:_ a suite that reports no groups (gtest, talos, jittest, ...)
  or a task-level failure (crash, timeout, harness error) has nothing finer to compare,
  so the whole task is compared by label as on the build path — except that a chunked
  label is reported as new rather than compared, since the chunk covers different tests
  on each push. These cannot be reproduced by re-running a manifest, but the agent can
  still identify the culprit commit.

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
address if set; test-repair mails only the team address -- never the culprit author or
the pushing developer, though the culprit is still named in the body. Its verdicts are
tracking for the hackbot team, so every verdict is mailed, intermittents included; what
reaches sheriffs is the agent's Slack message, and only when they have to act. Set
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
