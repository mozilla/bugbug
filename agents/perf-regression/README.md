# perf-regression agent

Investigates a public Firefox performance-test Perfherder regression bug, on desktop or
Android, proposes a source
fix, and validates it on Firefox try hardware. It makes at most **one candidate
push** and never submits unchanged baselines. Results and patches appear in the
existing Hackbot run dashboard. It does not comment on Bugzilla, upload to
Phabricator, or land changes.
Each investigation focuses on up to three unique regression metrics with
the largest reported percentage changes. Metrics can share a workload and CI job;
other alert metrics remain unverified.

It is a helper for the regression's author: it gathers the evidence, proposes
and measures one candidate fix, and reports. If the candidate brings the numbers
back, the report describes the root cause and the fix; otherwise it summarizes
the suspected root cause, what was tried and suggested next steps. Everything
it produces is for a person to review.

## Android

Android alerts are handled like desktop ones: the candidate is measured with the
alert's own Fenix task, for example
`test-android-hw-a55-14-0-aarch64-shippable/opt-browsertime-benchmark-speedometer3-mobile-fenix`.
Autoland runs Simpleperf native-profiling copies of Speedometer 3 only on the A55
phones (`sp3-p`, `profile_speedometer3.zip` with one profile per workload), so
emulator (`android-em-*`) alerts are investigated without profiles. Five
repetitions on the A55 pool take longer to schedule than desktop jobs, and
emulator results are noisier, which weakens the recovery verdict there.

## Workflow

1. Read the Bugzilla report, comments, `regressed_by` bugs and attachment metadata.
   Discover the alert ID from the Perfherder link or `mach try perf --alert` command;
   require an explicit `alert_id` if ambiguous. Check that the alert belongs to the bug.
2. Select up to three regression metrics by percentage, deduplicating
   identical platform/build/options/subtest signatures. Metrics that no CI task
   produces, or that lack enough historical jobs to validate, are skipped in
   favour of the next-largest and listed in `excluded_metrics`; if none remain,
   the run stops before any model or Try time is spent. Save their measurements,
   historical per-job data points, and the culprit
   push's patches. Resolve both alert revisions from Mercurial to Git through Lando.
3. Download before/after profiles per affected suite/platform/build combination,
   using **autoland only**. For Speedometer3, only dedicated native-profiling
   `sp3-p` jobs are eligible. Other suites prefer a native before/after pair, then fall back to Gecko
   profiles from the alert's exact measurement tasks or nearby profiling jobs
   with the suite in their task name. Never mix native and Gecko in one pair.
   Profiling labels must match the measurement task after removing profiling
   modifiers, preserving application and runtime variants such as `no-nv`.
   Each side records its actual and expected revision. Related-alert profiles
   are marked nearby when their source revisions differ from the target pair;
   task profiles with unverifiable provenance are skipped.
   The manifest records profile type and the reason for fallback. Search the 30
   autoland pushes before the culprit push and the culprit push plus the 29
   after it, nearest first. Skip expired artifacts and record task/run IDs and
   push distances (in autoland pushes). If no pair is available,
   continue with source/patch/alert evidence and explicitly report missing profiles.
   Discover public Firefox profile artifacts (`.json`, `.json.gz`, `.jslb.gz`,
   `.zip`) through the Taskcluster artifact listing for non-Speedometer suites.
   Unsupported formats, absent profiles, and suites whose task names cannot be
   matched are reported explicitly; no cross-platform substitutes are used.
4. Pin the source checkout to the **regressing revision**, including when the change
   has since been backed out. Claude reads the evidence, always uses `profiler-cli`, prioritizing regressing subtests and counting only
   samples within `sync,async` markers for Speedometer3 (including full-profile
   analysis). Other tests use their own measured intervals, established from the
   harness and profile markers. Before analysis, the agent reads the test and
   measurement/reporting code, identifies units, aggregation, warmups, measured
   intervals and relevant threads, and relates the culprit patch to the alert.
   It records source paths, marker names and limitations in its profile evidence.
   The agent edits an isolated worktree. Harness/infrastructure changes are
   allowed only when the shared Mozilla VCS tool verifies that every commit in
   the culprit push changes files exclusively under `testing/`, `python/mozperftest/`,
   `tools/tryselect/`, `taskcluster/`, or `.taskcluster.yml`. The manifest records `culprit_files` and
   `harness_changes_allowed`; mixed source/infrastructure pushes or unavailable
   file metadata do not qualify. The prompt and candidate validator use that
   same decision. Any harness correction must preserve the intended workload
   and measurement, rather than hide a browser regression. It can stop with
   `no_solution` without spending any CI time. The candidate starts from the
   bad revision and is a complete standalone patch.
5. Derive historical baselines from Perfherder data around the alert push. No
   baseline pushes are permitted. Good data comes from the newest pushes before
   the alert push until there are five jobs (at most five pushes); bad data from
   the alert push, extended to at most two following pushes only while it has
   fewer than two jobs, so a later backout is unlikely to be included. Resolve
   the selected metrics' original CI task labels from Taskcluster metadata,
   falling back to the exact signature's nearest historical Perfherder
   observations and their Treeherder jobs when metadata is missing. Labels must
   also appear in Treeherder's `alertsummary-tasks` response and match the
   metric's platform (`test-<platform>/…` for raptor and talos,
   `…-<platform>/opt` for mozperftest). Select one non-profiling job per metric and
   deduplicate shared jobs: at most three task labels, each with rebuild 5.
   Task selection is checked before profile collection. Profile search covers the 30 autoland
   pushes before the alert push and the alert push plus the 29 after it, found by
   push time (Treeherder push IDs are shared across repositories). Only alerts
   attributed to this summary count: downstream, reassigned-away, invalid and
   infra alerts are skipped, and related alerts only when reassigned here. At
   most three task labels are pushed (15 test runs with rebuild 5).
   Source patches use Hackbot's `changes.commit_all` and `changes.collect`, and
   culprit patches use the shared Mozilla VCS tool, capped at 2 MB each. Pushes
   with more than 50 changesets (typically mozilla-central merges) are
   investigated without culprit patches. Inaccessible regressor bugs are recorded
   as unavailable.
   Bug records, comments and attachment metadata use the shared Bugzilla read
   tools with the collector's asynchronous HTTP transport and read retries.
   The candidate is pushed with Hackbot's ordinary `try_server.push` action
   (see [Try push](#try-push)), whose try configuration comes from `lando-client`. Each candidate uses
   `rebuild: 5`. Validation needs at least two historical jobs per group, which
   selection already guarantees; an incomplete Try result is inconclusive
   while retaining available means, sample counts and observed improvements.
6. Before submission, run local whitespace, clang-format and lint checks on the
   changed files. Save output in `attempts/<n>/preflight.log`; failures or unavailable
   tooling block the push. The model is instructed to fix lint/format findings first.
   Both use `python -m hackbot_agents.perf_regression.preflight` as a wrapper for
   `./mach lint`. Both callers share the same checker and the Firefox tools’
   toolchain environment (`agent_tools.firefox_env`). It bootstraps the checkout's clang tools without configuring a
   Firefox build. Lint setup must succeed with the revision's original dependency
   pins; skipped checks and setup failures block submission even with exit code 0.
   Try lint-job failures are retained as `ignored_lint_failures` but do not reject
   the candidate or trigger another push. Selected performance failures and
   failures of their prerequisites (decision, build, toolchain, fetch and
   docker-image tasks) prevent recovery; available measurements from successful
   jobs are retained. Unrelated test failures do not reject the selected
   performance jobs. If every scheduled job finishes but measurements are still
   missing after the ingestion grace period, or CI times out, the available data
   is evaluated and the attempt cannot be marked fixed.
   Submit the candidate, persist continuation state, and exit. Pulse and the API
   resume validation once CI finishes, checking successful jobs and all selected
   measurements. Match platform, suite, subtest, build options, units, framework
   and score direction, not just the test name. Keep one observation per CI job.
7. Python reports every selected metric's observed improvement versus historical
   bad measurements, residual difference versus historical good measurements,
   the fraction of the regression recovered, sample counts, and informational
   confidence bounds (95% family-wise Welch bounds, with a variance floor for
   Perfherder's 0.01 rounding). Any positive mean change is reported as an
   improvement.
8. Stop after the single candidate try run and report its outcome. A metric is
   back to where it used to be when the candidate's mean recovers at least 80%
   of the gap between the historical bad and good means. All selected metrics
   must recover for `fixed`; partial improvements are also reported, together
   with the model's suggested next steps.
   `positive_attempts` identifies the candidate when any metric improves; its
   patch and per-metric results remain under `attempts/`. Mixed improvements and
   regressions remain visible individually. Missing data cannot establish recovery.
   The main results include a Markdown investigation report: the affected source
   paths/functions and processes/threads, the causal chain from the culprit to the
   metric, supporting observations and inferences, limitations, the candidate change,
   correctness checks and the CI verdict. It uses the existing dashboard Markdown
   findings view. The report and structured analysis remain available even when
   no candidate is found or validation cannot establish recovery.

Historical comparisons may be affected by changes in CI conditions since the alert.
The legacy `fixed` status and `verified/` artifact path indicate observed recovery
under this policy, not engineering approval.

Performance validation does not prove functional correctness. The model is
instructed to preserve the culprit's intended semantics, add appropriate tests,
and report the checks it actually ran. Patches still need engineering review.
Harness/CI edits require the exception above. Protected paths are
`testing/raptor/`, `testing/talos/`, `testing/mozharness/`, `testing/performance/`,
`python/mozperftest/`, `tools/tryselect/`, `taskcluster/` and `.taskcluster.yml`.
Benchmark-input and generated try-configuration edits remain forbidden. The
orchestrator checks the paths of the exact patch it pushes, after committing it.

### Try push

The candidate is pushed once, by the orchestrator rather than the model, through
the same `try_server.push` recorded action other agents use. The orchestrator
applies the candidate to the main checkout (so `changes/` holds the pushed
candidate) and records the action with the alert's tasks and `rebuild: 5`. The
investigation then exits; hackbot-api applies the action with its own Lando
credentials through the ordinary try-server handler, where it is audited like any
other action and held for a manual apply when the run requires review. The agent
job holds no Lando token. The model's shell sees neither the run's artifact upload
grant (`RESULTS_POLICY_*`) nor, locally, the developer's Lando token. The model
runs as the same user as the orchestrator, so this keeps out prompt-injected
commands, not a model determined to tamper with the orchestrator; that is
acceptable because the results are advisory and the report links the candidate
commit exactly as it was pushed to Try.

## Local run

Requires Docker, an Anthropic API key and a Lando user access token with
permission to push to `try`: with no hackbot-api to apply recorded actions, a local
run pushes the candidate itself with your token, as `mach try` would. Ordinary
Bugzilla reads are public and need no API key. Export them (or put them in the repository-root `.env` and add
`--env-file .env` to the command):

```sh
export ANTHROPIC_API_KEY=... LANDO_ACCESS_TOKEN=...
```

From the repository root, build and run just this compose project (avoids required
environment variables belonging to unrelated agents in the root compose file):

```sh
RUN_ID=perf-2075214 BUG_ID=2075214 docker compose \
  -f agents/perf-regression/compose.yml \
  up --build --abort-on-container-exit --exit-code-from perf-regression-agent
```

This command performs real, billable model calls and real try pushes. The example
resolves to alert 53155; it was backed out, so the pinned historical base matters.
The first source checkout is large. A local run then waits for CI in place: it polls
Treeherder every ten minutes (CI usually takes a few hours, `CI_TIMEOUT_SECONDS`
caps it at 24 hours by default), waits for Perfherder to ingest the results, and
exits once the report has its verdict. Deployed Hackbot instead exits after the push
and lets the API resume validation (see [CI completion events](#ci-completion-events)).

Optional agent inputs: `ALERT_ID`, `MAX_ATTEMPTS` (fixed at 1), `MODEL`, `MAX_TURNS`
(per candidate; default 500, range 1–500), `EFFORT` (default high), `CI_TIMEOUT_SECONDS`
(per push; default 86,400), and `RUN_ID`. Candidate pushes always use `rebuild: 5`,
with at most three task labels (15 test runs per push).

Artifacts live in `~/hackbot/artifacts/<run_id>/`:

- `summary.json`: the runtime's success/error state. Completed investigations
  include the outcome, description, attempts, measured confidence bounds and links.
- `investigation/`: bug/alert/history/culprit evidence, profile provenance,
  submission receipts and progress snapshots.
- `investigation/report.md`: readable investigation report, also stored in the
  `result` finding; updated on progress and retained if the investigation stops.
- `investigation/candidate-<n>-analysis.json`: the model's structured analysis,
  retained before local preflight and CI so failures do not discard its findings.
- `attempts/<n>/`: candidate patch, model log (`agent.log`) and local check output
  (`preflight.log`), retained for review.
- `verified/fix.patch`, `verified/description.txt`, and the runtime's `changes/`:
  produced only for the candidate that passed the performance gate.

Lint/setup, submission and CI-wait errors follow the same runtime failure path as
other agents: `summary.json` reports an error and the container exits with code 1.
Partial attempts, positive results and the stopping reason remain in
`investigation/progress.json`; candidate patches and logs are retained. Finding no
solution or completing the single attempt without recovery is a completed
investigation, not an error.

Model sessions do not resume after termination. CI validation uses the saved
`investigation/continuation.json`, proposal, baselines and candidate patch, and the
Lando job of the applied push; it never starts another model session or pushes
again. Disable automatic job retries.

## Automatic bug-creation trigger

The existing authenticated `POST /webhooks/bugzilla` endpoint recognizes `create`
events targeting a bug with explicit `is_private: false`, both `perf-alert` and
`regression` keywords, filed by an account in `BUGZILLA_WEBHOOK_PERF_REGRESSION_REPORTERS`
(default `["performance-sheriff-bot@mozilla.tld"]`, which files every Perfherder
regression bug). It submits `perf-regression` with the bug ID, the suites in
`BUGZILLA_WEBHOOK_PERF_REGRESSION_SUITES` (default `["speedometer3"]`; `[]` for every
suite) and the database dedupe key `perf-regression-created:<bug-id>`. Duplicate
deliveries resolve to the same run even across API instances. Modification events
retain the existing needinfo behavior. The agent never comments on the bug; results
stay in the Hackbot dashboard.

The run reads the alert itself and considers only regression rows of those
suites, even when another suite's regression is larger or names the bug. If there are
none, it finishes at once with an `out_of_scope` report, before any history or
profile download, checkout, model session or try push. Manual runs investigate every
suite unless given `suites`.

The route is **on by default** (`BUGZILLA_WEBHOOK_PERF_REGRESSION_ENABLED=false`
turns it off), so finish these steps before deploying a hackbot-api that receives
BMO creation events:

1. Deploy the Job from the `[deploy]` table in `hackbot.toml` (see
   [tools/hackbot-deploy](../../tools/hackbot-deploy/README.md)). It needs no Lando
   credential: hackbot-api pushes to try with the one its try-server handler
   already uses.
2. Set Cloud Run task retries to **zero**. The investigation uses an 8-hour
   execution limit and exits after recording its push, while CI can run for up to
   24 hours. Validation gets a separate one-hour execution and fresh signed
   artifact grants.
3. Keep `BUGZILLA_WEBHOOK_PERF_REGRESSION_ENABLED` unset (or `true`) on hackbot-api,
   and widen `BUGZILLA_WEBHOOK_PERF_REGRESSION_SUITES` when ready.
4. Configure BMO to deliver matching **bug creation** events to that endpoint using
   the configured `X-Bugzilla-Webhook-Secret`. Merely deploying the code does not
   create a BMO webhook subscription. All later classification/modification events
   are excluded; use the dashboard for bugs that acquire the keywords afterwards.

Manual API/UI runs work independently of the creation-event flag. Choose
`perf-regression` in the dashboard and supply the bug ID, or call
`POST /agents/perf-regression/runs` with `{"bug_id":2075214}`.

## Tests

```sh
uv run --package hackbot-agent-perf-regression --with pytest \
  --with pytest-asyncio pytest agents/perf-regression/tests
uv run --package hackbot-api --extra dev pytest services/hackbot-api/tests
```

Tests use recorded-shaped data and mocked network/model/try clients. They cover
signature identity, missing/invalid observations, score direction, controls,
confidence bounds, attempt limits, the recorded try push and its application, CI
completion, event filtering and durable API dedupe. Real model/try execution and
Cloud Run deployment require the credentials above and are separate integration
checks.

If a candidate session ends normally without submitting a result (for example,
while waiting for background lint), the orchestrator continues the same session
with up to three wrap-up prompts, bounded by a shared 30-minute recovery timeout.
SDK errors before a result is recorded stop the investigation and include their
error type in the failure summary. A recorded proposal survives a late SDK error.

### CI completion events

The investigation publishes its evidence, proposal, patch, historical baselines,
recorded try push and deadline, then exits. The API keeps the same
run in `awaiting_ci`, with the investigation and artifacts visible on its existing
page. No agent execution is running while CI is pending.

Pulse terminal-task events arrive through Pub/Sub at the authenticated
`POST /internal/events/try-task-finished` endpoint. The API correlates the saved
Lando receipt to its Try revision and task group and checks authoritative group
status. When CI finishes, it starts a short validation execution of the same
image under the same run ID. It never invokes the model or submits another
patch. It measures the metrics chosen before the push, even if sheriffs
re-triage the alert meanwhile, against the baselines saved with them, and links
the candidate commit as pushed to Try. Only a recovered candidate reaches
`verified/` and canonical `changes/` artifacts. Final completion notifications
are sent after validation, not when the investigation exits.

Once Taskcluster is done, validation waits up to 15 minutes for Perfherder
ingestion within its own execution. It hands back to the API only if
Treeherder still shows CI running (for example after retriggers); the API then
relaunches with exponential backoff (5 minutes doubling to an hour). Tasks that
can never run because a dependency failed count as finished, so a broken build
is reported promptly instead of at the deadline. The sweep also handles events
that arrived before registration, lost events, Lando failures, and the 24-hour
deadline. Database claims keep a run to one validation launch at a time, and CI
is read without holding a database lock. A failed or unregistered launch is
retried with the same backoff; after five failures, or six hours past the
deadline, the run fails with the reason. A validation execution that crashes,
times out or reports an error is relaunched the same way, up to three times.
An ambiguous launch error can occasionally start a second validator for the
same push. Each validation attempt receives an upload grant restricted to
`_validation/<validation-id>/` under the run. Only after the tracked execution
finishes successfully does the API, holding the run's database lock, copy that
attempt's outputs to the usual run artifact keys. It writes `summary.json`
last and preserves canonical artifact URLs. Untracked or late executions cannot
overwrite those files or another attempt's outputs. Pending CI snapshots are
published the same way so later checks resume the accepted state; failed
attempts' partial outputs are not published. Retriggers that land in another
task group are not watched; the sweep and validation still see them through
Treeherder.

Deployment setup (required for automatic validation):

- Apply the API's Alembic migrations, including the nullable `runs.ci_wait` column,
  **before** deploying the API: the API reads that column for every run, so an
  API deployed first fails for all agents.
- Deploy the agent, API and Pulse listener together. Give the API permission to
  execute the existing agent job and sign/download its artifacts, as for other runs.
- Deploy Pulse with `CI_EVENTS_TOPIC=projects/PROJECT/topics/ci-task-events`.
  Its deployment script provisions the topic and publisher permission.
- Create a Pub/Sub **push** subscription on that topic targeting
  `https://API/internal/events/try-task-finished`, using the API's existing
  `PUSH_AUTH_SERVICE_ACCOUNT` identity and `PUSH_AUTH_AUDIENCE`. This endpoint
  uses the same OIDC verification as the other internal event endpoints.
- Schedule the existing `POST /internal/maintenance/finalize-stale-runs?min_age_minutes=1&limit=1000`
  every five minutes with the same authenticated push identity. CI waits are
  included regardless of age. This is a required safety net for early/missed
  events and indexing delays; it does not keep any agent job alive.

These deployment resources are configured outside this repository, as with the
existing Cloud Run completion-event and maintenance subscriptions. Local Compose
runs wait for CI in place instead.
