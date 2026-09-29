# Performance Alerts Data Pipeline

This pipeline builds the datasets used for performance test selection and for
the performance regression predictor. It snapshots Perfherder alerts and the
pushes they refer to, applies one labeling policy, and publishes versioned
databases like the rest of the bugbug data pipeline.

Everything is produced by the `bugbug-data-perf-alerts` command, defined in
`scripts/perf_alerts_retriever.py`, with the logic in the `bugbug.perf`
package.

## Code layout

| Module                               | Role                                                                  |
| ------------------------------------ | --------------------------------------------------------------------- |
| `bugbug/redash.py`                   | generic client for ad-hoc SQL on STMO, reusable outside this pipeline |
| `bugbug/perf/__init__.py`            | public API and dataset registration; `from bugbug import perf`        |
| `bugbug/perf/label_policy.py`        | every hand-maintained table that decides labels, and `POLICY_VERSION` |
| `bugbug/perf/task_labels.py`         | pure functions: label parsing, grouping hierarchy, exclusions         |
| `bugbug/perf/extraction.py`          | query builders and fetch/parse for Treeherder, `fxci` and Bugzilla    |
| `bugbug/perf/sql/`                   | the SQL templates                                                     |
| `bugbug/perf/datasets.py`            | dataset paths, registrations and readers                              |
| `bugbug/perf/regression_labeling.py` | `PushIndex`, culprit pinning, labeled regression records              |
| `bugbug/perf/push_status.py`         | per-push label state and settled flag; the shared source of negatives |
| `bugbug/perf/selection_pairs.py`     | test selection pairs and the labeled pair stream                      |
| `bugbug/perf/regression_commits.py`  | commit-level dataset for the regression predictor                     |
| `bugbug/perf/task_costs.py`          | joining pushes to task costs                                          |

Dependencies point one way: `label_policy` imports nothing from the package,
`task_labels` reads its tables, `extraction` and `regression_labeling` build on
both, `push_status` builds on `regression_labeling`, and `selection_pairs` and
`regression_commits` both take their negatives from `push_status`.

## Sources

| Source                              | Access                            | What it provides                                                                                                                                                          |
| ----------------------------------- | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Treeherder Postgres replica         | STMO (Redash) data source 89, SQL | Perfherder alerts, summaries and their statuses, signatures, pushes, commits, performance jobs                                                                            |
| BigQuery `fxci` dataset             | STMO (Redash) data source 63, SQL | Worker pool, runs, machine time and attributed cloud cost of every performance task                                                                                       |
| Bugzilla                            | REST API, optional token          | The regression bug of each summary and the canonical bugs its duplicates point at, with `regressed_by` to pin the culprit commit and the resolution to decide the outcome |
| Mercurial clone (`mozilla-central`) | `hg export`                       | Commit messages and diffs for the regression predictor dataset                                                                                                            |

Only autoland is retrieved by default (`REPOSITORIES` in `label_policy.py`,
overridable with `--repository`). Sheriffs attribute regressions to autoland
pushes, and a mozilla-central push is a merge whose commit list repeats every
autoland commit it merged, so including it produced no positives and turned
culprit commits into negatives through their merge push.

The Redash key is read from `REDASH_API_KEY`, or from the `REDASH_API_KEY`
entry of the Taskcluster secret when running in the pipeline. An optional
`BUGBUG_BUGZILLA_TOKEN` lifts Bugzilla rate limits.

The alert-to-task mapping is done in SQL by joining each alert's signature to
the datapoints on the summary's push and from there to the job and its type.
Treeherder deletes jobs after 120 days, so the source can only map alerts from
the last four months, and it currently holds alert summaries for about
fourteen months. The alerts snapshot therefore acts as an archive: every run
refetches whatever the source still has and merges it into the previous
snapshot, keeping rows the source no longer returns and carrying forward task
labels captured while the job still existed. Mapped history grows by a
fortnight per run; only alerts already older than four months on the first
run stay unmapped.

## Operations

`retrieve` snapshots the sources into three raw databases:

- `data/perf_alerts.json`: one row per alert, attached to the summary it
  currently belongs to (reassigned alerts follow `related_summary_id`), with
  summary, signature, platform, push and task label fields. Refetched whole
  and merged into the previous snapshot, see Sources.
- `data/perf_pushes.json`: one row per autoland push,
  with its commits (revision, first line, bug id, backout flag) and the
  performance jobs that ran on it (label, tier, total duration, and one entry
  per run with task id, retry id, result and duration).
  Pushes are retrieved incrementally in short windows and start three weeks
  before the alerts window, since Perfherder raises alerts up to two weeks
  after the culprit push; those lead pushes can be culprits but are never
  negatives, because their own alerts predate the snapshot.
  the most recent `--reretrieve-days` are refreshed on every run because jobs
  keep completing.
- `data/perf_alert_bugs.json`: the regression bugs referenced by summaries,
  plus the canonical bugs reached by following `dupe_of` up to three hops.
- `data/perf_task_costs.json`: one row per performance task from `fxci`,
  with the worker pool (`task_queue_id`), run count, machine time and, for
  cloud hosted pools only, the attributed dollar cost. Hardware pools such as
  `releng-hardware/*` and `proj-autophone/*`, which run almost every
  performance test, have no cloud billing, so their `cost` is null and machine
  time per pool is the cost unit. Rows keep only the source columns; derive
  test identities with `get_runnable_identity(label)`. Joins to the pushes
  snapshot on the task id of each job run. Like pushes, costs are retrieved
  incrementally: rows older than `--reretrieve-days` are kept and the recent
  window is refetched. Expect roughly 300 bytes per task, so a full history is
  a few gigabytes uncompressed; read it with `load_task_costs(task_ids)`
  restricted to the pushes you need.

`generate` applies the labeling policy and writes
`data/perf_regressions.json`: one record per confirmed regression summary with
the culprit push and revisions, its landings, the pinned culprit commits, and
the regressed runnables in both signature and normalized task label form.

`generate` also writes the thin test selection datasets, at commit
granularity so that landing-level and push-level views can be derived by
grouping (the reverse is impossible):

- `data/perf_scheduling_pairs.json`: one row per culprit commit and test
  identity (the platform-independent test name), merging every platform and
  summary that regressed it. Each row carries `push_id`, `landing_id` (the
  commit's bug within its push) and `landing_size`, plus `family`, `category`,
  `framework`, `application`, `platform_families`, `platform_labels`,
  `summary_ids` and `pinned_by`. These are the positives. Pinned regressions
  label their culprit commits only; unpinned ones label every non-backout
  commit of the push with `pinned_by` null so consumers can filter them.
- `data/perf_push_status.json`: one row per push with `settled`, its
  non-backout `commits` (node, bug id, landing id and size), and the test
  identities and platform families that ran. Negatives are not stored: every
  candidate identity on every commit of a settled push that is not a culprit
  push is a clean negative, and `perf_alerts.iter_labeled_pairs()` yields
  positives and inferred negatives together. Model features are deliberately
  not part of these files.

`export` reads the labeled regressions and the push status rows, joins them to
the Mercurial clone and writes
`data/perf_regression_commits.json`: one row per commit with `commit_message`,
`diff`, `label`, `landing_id`, `landing_size`, `pinned_by`, `summary_ids` and a
time based `split`. Negatives are sampled at `--negative-ratio` times the number of
positives from the clean settled commits in `perf_push_status.json`, the same
rule the selection pairs use, never a commit that is a culprit elsewhere.
Commits whose diff is empty, such as merges, are skipped.

Flag defaults are the `DEFAULT_*` constants at the top of
`scripts/perf_alerts_retriever.py`; the pipeline tasks pass no flags, so those
are the production values. `generate` and `export` share the window flags.
`--settle-days` (default 21) drops pushes younger than that, since their alerts
may not have fired or been triaged yet. `--since DATE` and `--until DATE`
restrict the dataset to pushes in that range, so a narrower or reproducible
dataset needs no new retrieval; `--until` never extends past the settle cutoff.
Without `--since`, culprits start with the first fetched alert and negatives
with the first alert summary in the snapshot, since earlier pushes may have
alerts the snapshot lacks. `export` takes `settled` from the push status rows,
so it can narrow the window `generate` used but not widen it.

## Dataset schemas

All datasets are newline-delimited JSON, one record per line, compressed as
`.zst` when published. The raw query columns are documented in the headers of
the SQL files under `bugbug/perf/sql/`; the tables below describe the records
as stored, after the reshaping done in `bugbug/perf/extraction.py`. Types use
`?` for nullable fields and `ts` for ISO 8601 timestamps.

### `perf_alerts.json` (one record per alert)

Every column of `perf_alerts.sql`, plus the decoded and split fields at the end
of the table. An alert belongs to the summary it is currently attached to, so a
reassigned alert carries its new summary's fields.

| Field                                              | Type            | Meaning                                                                                                                      |
| -------------------------------------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `alert_id`                                         | int             | Perfherder alert id                                                                                                          |
| `alert_status`                                     | int             | 0 untriaged, 1 downstream, 2 reassigned, 3 invalid, 4 acknowledged, 5 confirming                                             |
| `is_regression`                                    | bool            | direction of the shift; false is an improvement                                                                              |
| `amount_pct`, `amount_abs`                         | float           | size of the shift, relative and absolute                                                                                     |
| `t_value`                                          | float           | Student t statistic the detector used to raise the alert                                                                     |
| `prev_value`, `new_value`                          | float           | series value before and after the shift                                                                                      |
| `noise_profile`                                    | str             | `OK`, `OUTLIERS`, `SKEWED`, `MODAL` or `N/A`; how noisy the series is                                                        |
| `alert_manually_created`                           | bool            | created by a sheriff rather than the detector                                                                                |
| `alert_sheriffed`                                  | bool            | the series is part of the sheriffed set                                                                                      |
| `alert_summary_id`, `related_summary_id`           | int, int?       | summary the alert was created under, and the one it was reassigned to                                                        |
| `alert_created`                                    | ts              | when the detector raised it                                                                                                  |
| `summary_id`                                       | int             | the summary it currently belongs to: `related_summary_id` when set, else `alert_summary_id`                                  |
| `summary_status`                                   | int             | 0 untriaged, 1 downstream, 2 reassigned, 3 invalid, 4 improvement, 5 investigating, 6 wontfix, 7 fixed, 8 backedout, 9 infra |
| `summary_created`, `first_triaged`, `last_updated` | ts, ts?, ts     | summary opened, first sheriff action, last change                                                                            |
| `bug_number`, `bug_status`, `bug_updated`          | int?, int?, ts? | linked bug, Perfherder's cached Bugzilla status code for it, and when it was linked                                          |
| `summary_manually_created`, `summary_sheriffed`    | bool            | created by hand; false for automated monitor summaries                                                                       |
| `notes`                                            | str?            | free-text sheriff notes                                                                                                      |
| `push_id`, `prev_push_id`                          | int             | culprit push and the last push before it with a datapoint                                                                    |
| `original_push_id`, `original_prev_push_id`        | int             | the same pair as first detected, before any sheriff edit                                                                     |
| `revision`, `push_time`                            | str, ts         | culprit push as changeset hash and timestamp                                                                                 |
| `prev_push_revision`, `prev_push_time`             | str?, ts?       | previous push; null for a series' first datapoint                                                                            |
| `repository`                                       | str             | `autoland` (see `--repository`)                                                                                              |
| `framework`                                        | str             | `browsertime`, `talos`, `awsy`, `mozperftest`, `build_metrics`, ...                                                          |
| `signature_id`, `signature_hash`                   | int, str        | Perfherder series id and its stable identity hash                                                                            |
| `suite`, `test`                                    | str             | e.g. `amazon` and `fcp`; `test` is empty for a suite summary                                                                 |
| `extra_options`                                    | str             | space-separated options such as `cold fission webrender`                                                                     |
| `application`                                      | str             | `firefox`, `chrome`, `fenix`, ...; empty for some frameworks                                                                 |
| `lower_is_better`                                  | bool            | direction in which the metric improves                                                                                       |
| `should_alert`                                     | bool?           | whether the series is configured to alert at all                                                                             |
| `alert_severity`                                   | str             | `critical`, `subcritical` or `normal`, as set in-tree                                                                        |
| `measurement_unit`                                 | str?            | `ms`, `score`, `bytes`, ...                                                                                                  |
| `signature_tags`                                   | str             | space-separated tags on the series                                                                                           |
| `platform`                                         | str             | machine platform, e.g. `linux2404-64-shippable`                                                                              |
| `summary_status_name`, `alert_status_name`         | str             | the two numeric statuses decoded, e.g. `fixed`, `acknowledged`                                                               |
| `summary_tags`                                     | list[str]       | sheriff tags on the summary, e.g. `harness` or `infra`, split from the pipe-joined column                                    |
| `task_labels`, `task_ids`                          | list[str]       | task labels and Taskcluster task ids of the datapoints that triggered the alert, split from the pipe-joined columns          |
| `normalized_labels`                                | list[str]       | `task_labels` after platform renames, deduplicated and sorted                                                                |

### `perf_pushes.json` (one record per push)

| Field                | Type | Meaning                                                                                                       |
| -------------------- | ---- | ------------------------------------------------------------------------------------------------------------- |
| `push_id`            | int  | Treeherder push id                                                                                            |
| `repository`         | str  | `autoland` (see `--repository`)                                                                               |
| `revision`           | str  | head changeset hash                                                                                           |
| `time`               | ts   | when the push landed                                                                                          |
| `author`             | str? | who pushed                                                                                                    |
| `commits[]`          | list | in push order: `revision`, `desc` (first line), `bug_id` (int?, parsed from the first line), `backout` (bool) |
| `perf_jobs[]`        | list | one entry per perf task label that ran: `label`, `tier` (int?), `duration` (total seconds), `runs[]`          |
| `perf_jobs[].runs[]` | list | one entry per run: `task_id` (str?), `retry_id` (int?), `result` (str), `duration` (int?)                     |

### `perf_alert_bugs.json` (one record per bug linked from a summary)

Bugzilla REST fields, stored unchanged. Bugs linked on improvement summaries
are included, and those often have an empty `regressed_by` because the linked
bug is the change itself. Bugs reached by following `dupe_of` are included too.

| Field                               | Type      | Meaning                                                                              |
| ----------------------------------- | --------- | ------------------------------------------------------------------------------------ |
| `id`                                | int       | bug number                                                                           |
| `summary`                           | str       | bug title                                                                            |
| `status`, `resolution`              | str       | live Bugzilla state, e.g. `RESOLVED` and `WONTFIX`; `resolution` is empty while open |
| `product`, `component`              | str       | where the bug is filed                                                               |
| `keywords`                          | list[str] | e.g. `perf-alert`, `regression`                                                      |
| `creation_time`, `last_change_time` | ts        | when filed and last modified                                                         |
| `regressed_by`                      | list[int] | bugs the developer named as the cause; the first source of truth for culprit pinning |
| `regressions`                       | list[int] | bugs this one is recorded as having caused                                           |
| `dupe_of`                           | int?      | the bug this one was closed as a duplicate of                                        |
| `depends_on`, `blocks`              | list[int] | dependency links, kept for reference                                                 |

### `perf_task_costs.json` (one record per perf task)

The columns of `perf_task_costs.sql` restricted to `COST_FIELDS`; the query's
`test_platform` and `test_suite` tags are dropped since `label` carries the
same information. Derive identities with `get_runnable_identity(label)` when
reading.

| Field             | Type   | Meaning                                                                                                  |
| ----------------- | ------ | -------------------------------------------------------------------------------------------------------- |
| `task_id`         | str    | Taskcluster task id; joins to `task_ids` in the alerts and to `perf_jobs[].runs[].task_id` in the pushes |
| `label`           | str    | task label, e.g. `test-linux2404-64-shippable/opt-talos-g1`                                              |
| `project`         | str    | `autoland` (see `--repository`)                                                                          |
| `task_queue_id`   | str    | worker pool, e.g. `releng-hardware/gecko-t-linux-talos-2404`; also names the machine type                |
| `submission_date` | str    | day the task was created, `YYYY-MM-DD`                                                                   |
| `runs`            | int?   | runs that started and finished, retries included                                                         |
| `duration`        | int?   | total machine seconds across those runs                                                                  |
| `cost`            | float? | attributed dollars; null for hardware pools, which have no cloud billing                                 |
| `costed_runs`     | int?   | runs that contributed to `cost`; null when `cost` is null                                                |
| `cloud_provider`  | str?   | `gcp` or `azure` when costed, otherwise null                                                             |

### `perf_regressions.json` (one record per confirmed regression summary)

| Field                              | Type         | Meaning                                                                                                                                                 |
| ---------------------------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `summary_id`                       | int          | the Perfherder summary                                                                                                                                  |
| `repository`, `framework`          | str          | where and which harness                                                                                                                                 |
| `status`                           | str          | sheriff's summary status: `investigating`, `wontfix`, `fixed` or `backedout`                                                                            |
| `resolved`                         | bool         | true for the three terminal summary statuses                                                                                                            |
| `summary_tags`                     | list[str]    | sheriff tags, e.g. `harness`                                                                                                                            |
| `outcome`                          | str          | `concluded`, `attributed`, `pending` or `rejected`; only the first two are positives, see the labeling policy                                           |
| `created`                          | ts           | summary creation                                                                                                                                        |
| `bug_number`, `bug_status`         | int?, int?   | linked bug and Perfherder's cached status code                                                                                                          |
| `canonical_bug`                    | int?         | the bug reached by following `dupe_of` when the linked bug is a duplicate                                                                               |
| `bug_state`, `bug_resolution`      | str?, str?   | live Bugzilla status and resolution of the canonical bug, e.g. `RESOLVED` and `WONTFIX`                                                                 |
| `push_id`, `revision`, `push_time` | int, str, ts | the culprit push, taken as accurate                                                                                                                     |
| `reassigned`                       | bool         | a sheriff moved the summary from its original push                                                                                                      |
| `revisions`                        | list[str]    | all commits of the culprit push, in order                                                                                                               |
| `landings[]`                       | list         | commits grouped by bug: `landing_id`, `bug_id`, `backout`, `revisions`, `culprit`; backouts are included and flagged, since a revert can be the culprit |
| `culprit_commits`                  | list[str]    | commits pinned as the cause; empty when unpinned                                                                                                        |
| `pinned_by`                        | str?         | `bug` (via `regressed_by`), `summary_bug` (the summary links the culprit bug), `single_landing`, or null                                                |
| `regressed_by_agrees`              | bool?        | whether the linked bug's `regressed_by` landed in the culprit push; null when there is nothing to compare                                               |
| `runnables[]`                      | list         | one entry per regressed test series, see below                                                                                                          |
| `policy_version`                   | int          | rules version that produced the record                                                                                                                  |

Each `runnables[]` entry is one regressed test series, deduplicated per
signature and task label:

| Field                                      | Type              | Meaning                                                                |
| ------------------------------------------ | ----------------- | ---------------------------------------------------------------------- |
| `alert_id`, `alert_status`                 | int, str          | the alert and its decoded status                                       |
| `signature_id`, `signature_hash`           | int, str          | the Perfherder series                                                  |
| `framework`, `suite`, `test`               | str               | harness, page or benchmark, and metric, as in `perf_alerts.json`       |
| `platform`, `extra_options`, `application` | str               | where it ran, its options, and the browser under test                  |
| `amount_pct`, `t_value`, `noise_profile`   | float, float, str | size and confidence of the shift, and series noisiness                 |
| `label`                                    | str?              | normalized task label that produced the datapoints; null when unmapped |
| `test_name`, `family`, `category`          | str?              | grouping levels from the label; null when unmapped                     |
| `platform_family`                          | str               | OS family, from the label when mapped, else from `platform`            |

### `perf_scheduling_pairs.json` (one record per culprit commit and test identity)

| Field                                            | Type            | Meaning                                                                            |
| ------------------------------------------------ | --------------- | ---------------------------------------------------------------------------------- |
| `repository`, `push_id`, `revision`, `push_time` |                 | the culprit push (`revision` is its head)                                          |
| `node`, `bug_id`, `backout`                      | str, int?, bool | the commit, the bug parsed from its message, and whether it is a backout or revert |
| `landing_id`, `landing_size`                     | str, int        | `push_id:bug_id` and how many commits share it; group on it for landing rows       |
| `pinned_by`                                      | str?            | how the commit was pinned; null when the whole push was labeled                    |
| `test_name`                                      | str             | platform-independent test identity, e.g. `browsertime-tp6-firefox-amazon`          |
| `family`, `category`                             | str             | grouping levels, e.g. `browsertime-tp6` and `page-load`                            |
| `framework`, `application`                       | str             | harness and browser under test                                                     |
| `label`                                          | int             | always 1; negatives are inferred, see `perf_push_status.json`                      |
| `platform_families`                              | list[str]       | platform families that regressed, e.g. `["linux", "windows"]`                      |
| `platform_labels`                                | list[str]       | the normalized task labels that regressed                                          |
| `summary_ids`                                    | list[int]       | summaries merged into this pair                                                    |
| `policy_version`                                 | int             | rules version                                                                      |

### `perf_push_status.json` (one record per push)

| Field                                            | Type      | Meaning                                                                                                                                                 |
| ------------------------------------------------ | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repository`, `push_id`, `revision`, `push_time` |           | the push                                                                                                                                                |
| `label_state`                                    | str       | `culprit` (has a positive regression), `pending`, `rejected`, or `clean`; only settled clean pushes are negatives                                       |
| `settled`                                        | bool      | inside the labeled window: older than the settle window and not before the first alert summary in the snapshot, whose alerts would otherwise be missing |
| `commits[]`                                      | list      | all commits: `node`, `bug_id`, `backout`, `landing_id`, `landing_size`                                                                                  |
| `identities_ran`                                 | list[str] | test identities of in-scope perf tasks that ran                                                                                                         |
| `platform_families_ran`                          | list[str] | platform families those tasks ran on                                                                                                                    |
| `policy_version`                                 | int       | rules version                                                                                                                                           |

A settled push whose `label_state` is `clean` is a negative for every
candidate identity, and so is each of its non-backout commits;
`iter_labeled_pairs()` materializes those negatives on the fly. Pending and
rejected pushes yield neither positives nor negatives. At prediction time, commit scores
are aggregated to the push by taking the maximum per test.

### `perf_regression_commits.json` (one record per commit)

| Field                                   | Type           | Meaning                                                                                     |
| --------------------------------------- | -------------- | ------------------------------------------------------------------------------------------- |
| `node`                                  | str            | changeset hash                                                                              |
| `repository`, `push_id`, `pushdate`     | str, int, ts   | the push the commit landed in                                                               |
| `bug_id`                                | int?           | bug parsed from the commit message                                                          |
| `landing_id`, `landing_size`, `backout` | str, int, bool | the commit's landing, and whether it is a backout; weight or group by landing when training |
| `label`                                 | int            | 1 for culprit commits, 0 for sampled clean commits                                          |
| `pinned_by`                             | str?           | as in `perf_regressions.json`; null for negatives and unpinned positives                    |
| `summary_ids`                           | list[int]      | summaries that named this commit; empty for negatives                                       |
| `split`                                 | str            | `train` or `test`, the newest tenth by push date                                            |
| `commit_message`                        | str            | full message from `hg export`, header lines removed                                         |
| `diff`                                  | str            | the git-style diff from `hg export`                                                         |
| `policy_version`                        | int            | rules version                                                                               |

## Labeling policy

The rules live in `bugbug/perf/label_policy.py` and are stamped on every derived
record as `policy_version`. Bump it when they change.

- A summary enters labeling when its status is `investigating`, `wontfix`,
  `fixed` or `backedout` and it is not tagged `infra`. Harness-caused
  regressions are kept: a harness change is a repository change the scheduler
  must react to, and `summary_tags` lets a consumer filter them.
- Whether it counts is then decided by the developer's verdict on the
  regression bug, which is far more current than the sheriff's summary status
  (most confirmed summaries stay `investigating` forever). Duplicates are
  followed to their canonical bug first. The `outcome` is:
  - `concluded`: the bug was resolved `FIXED`, `WONTFIX` or `WORKSFORME`.
    `WORKSFORME` is kept because the metric did move even if nobody acted.
  - `attributed`: the sheriff linked the culprit bug itself instead of filing a
    regression bug, or closed the summary as `wontfix`, `fixed` or `backedout`
    without a bug. The bug's status describes the culprit change there, not
    the regression, so the link itself is taken as the conclusion.
  - `pending`: the bug is still open, the summary is `investigating` with no
    bug, or the duplicate's canonical bug is unknown.
  - `rejected`: the bug was resolved `INVALID`, `INACTIVE`, `INCOMPLETE` or
    `MOVED`: developers concluded it was not a regression.
    Only `concluded` and `attributed` summaries are positives. `pending` and
    `rejected` ones make their push neither a positive nor a negative
    (`label_state` in `perf_push_status.json`). Bugs resolve a median of eight
    days and up to three weeks after the summary, so pending records promote to
    concluded on later pipeline runs.
- Within a confirmed summary, an alert counts when `is_regression` is set and
  its status is not `downstream` or `invalid`. Summaries without such alerts are
  dropped.
- Summaries whose culprit push is younger than `--settle-days` are skipped, as
  are such pushes in push data and negative sampling. Both bounds are on push
  time, so a push counted as settled never has an alert that was dropped as
  too recent; alerts fire up to two weeks after the push.
- The culprit push named by the summary is taken as accurate. No attribution
  range or confidence weighting is applied.
- Within the culprit push, commits are grouped into landings (one bug's
  commits), backouts included and flagged. The culprit landing is pinned when
  the regression bug's `regressed_by` names a bug landed in the push, when the
  summary links the culprit bug directly, or when the push holds a single
  landing (a backout beside a real landing does not count). Otherwise every
  commit of the push is labeled with `pinned_by` null. Each regression also
  records `regressed_by_agrees`, whether the bug link and the summary name the
  same push, as a quality signal.
- Backout commits are never used as negatives.
- Negatives are inferred, not observed: any settled push that is not a culprit
  is clean for every sheriffed test, because the backstop runs every
  performance test and any regression would have been detected and attributed.
  This inference only holds while the backstop keeps running the full
  performance suite.

Task labels are normalized by dropping the `-qr` platform suffix and folding
retired platforms into their successors through `PLATFORM_RENAMES`.

### Exclusions

Runnables out of scope for Firefox perf test selection are dropped before
labeling, and a summary made only of excluded runnables is not a regression:

- summaries tagged `infra`, since infrastructure changes are not in any push;
- the `build_metrics` framework, which measures builds rather than tests, and
  the `js-bench` framework, the SpiderMonkey shell benchmarks run from
  `source-test-jsshell-bench-*` tasks outside the perf harnesses;
- tasks running a non-Firefox application (Chrome, Chromium, Safari), which a
  Firefox change cannot regress;
- profiling variants (`-profiling`, `-native-profiling`), which are
  diagnostics, listed in `EXCLUDED_VARIANTS`;
- the synthetic `regression-tests` canaries.

`get_exclusion_reason(label, framework, application)` returns which rule applied.

## Grouping hierarchy

Every perf task label is described at four levels by `get_runnable_identity`:

| Level                       | Example                                                          | Derived by                                                           |
| --------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------------------- |
| task label                  | `test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon` | normalization                                                        |
| test identity (`test_name`) | `browsertime-tp6-firefox-amazon`                                 | label minus platform and build type                                  |
| family                      | `browsertime-tp6`                                                | the kind-file entry: harness plus test, before the application token |
| category                    | `page-load`                                                      | ordered keyword rules in `CATEGORY_RULES`                            |

The platform is a separate axis, `platform_family` (linux, windows, macosx,
android), because most regressions are platform specific. Datasets are keyed
on the test identity; family and category are attached as features so rare
tests borrow strength from their siblings. `split_test_name` also exposes the
`application` and the diagnostic `variant`.

Mozperftest labels do not follow the `test-<platform>/<build>-<test>` shape:
the platform comes first and, on Linux, again at the end, as in
`perftest-android-hw-a55-aarch64-shippable-startup-fenix-cold-main-first-frame`
or `perftest-linux-service-worker-linux2404-64-shippable/opt`.
`split_perftest_label` strips the platform tokens so the identity is
`perftest-startup-fenix-cold-main-first-frame` or `perftest-service-worker`,
comparable across platforms like every other test.

### Glossary

Mozilla uses two vocabularies that disagree with each other: Perfherder, where
alerts come from, says framework, suite, test; the task graph and harnesses,
where tests are defined, say harness, kind entry, app, variant. Our identity
fields are parsed from task labels, so they follow the task graph where the two
differ. For `test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon`:

| Our field                          | Example                          | Perfherder                           | Task graph / harness                                    |
| ---------------------------------- | -------------------------------- | ------------------------------------ | ------------------------------------------------------- |
| `framework` (from the alert)       | `browsertime`                    | framework                            | harness; Raptor and Browsertime report as one framework |
| `test_name`                        | `browsertime-tp6-firefox-amazon` | none: one task feeds many signatures | the label minus its platform                            |
| `family`                           | `browsertime-tp6`                | none                                 | the kind-file entry; roughly Raptor's test type         |
| `category`                         | `page-load`                      | none                                 | none; ours, for the fallback                            |
| `subtest` (`split_test_name` only) | `amazon`                         | suite: the page or benchmark         | app-specific tail of the test name                      |
| `application`                      | `firefox`                        | application                          | app                                                     |
| `variant`                          | null (`swr`, `profiling`)        | inside `extra_options`               | test variant from `variants.yml`                        |
| `platform`                         | `linux2404-64-shippable/opt`     | platform + option collection         | test platform                                           |
| `build_type`                       | `opt`                            | option collection                    | build type                                              |
| `platform_family`                  | `linux`                          | none                                 | none; ours                                              |
| none                               | `fcp`, `loadtime`                | test: one metric inside a suite      | below the task; kept on runnables as `suite` and `test` |

Signature names cannot be turned into labels: the Talos suite `glterrain` runs
in the task `talos-webgl`, and the AWSY suite `Heap Unclassified` in
`awsy-tp6`. That is why the alerts query maps each alert to its job's task
label instead.

## Reading the data

```py
from bugbug import db, perf

db.download(perf.PERF_REGRESSIONS_DB)
for regression in perf.get_perf_regressions():
    print(regression["revision"], regression["pinned_by"], len(regression["runnables"]))

db.download(perf.PERF_PAIRS_DB)
db.download(perf.PERF_PUSH_STATUS_DB)
for pair in perf.iter_labeled_pairs(repository="autoland"):
    # Positives carry family, category and platforms; negatives are inferred.
    print(pair["push_id"], pair["node"], pair["landing_id"], pair["test_name"], pair["label"])

db.download(perf.PERF_REGRESSION_COMMITS_DB)
for commit in perf.get_perf_regression_commits():
    print(commit["label"], commit["landing_size"], commit["split"])
```

## Cost for evaluation

`perf.load_task_costs()` indexes the costs snapshot by task id and
`perf.summarize_push_cost(push, costs)` summarizes a push from the pushes
snapshot into machine seconds per worker pool plus the known cloud cost. The
`generate` operation logs how many perf runs could be matched to a pool and to
a cloud cost, so a drop in coverage is visible in the task log.

A shadow scheduler's choices never ran, so they cannot be priced from what
ran. `perf.build_label_prices()` turns the snapshot into a price list instead: for
each normalized label, its usual pool and median run duration, valid on any
push because a task's duration barely depends on the commit under test.
`perf.estimate_labels_cost(labels, prices)` then estimates machine seconds per pool for
any set of labels, falling back to the median of the same family on the same
platform family for labels with no history. Its output has the same
`machine_seconds_by_pool` shape as `summarize_push_cost`, so a strategy's cost, the
actual cost and the full-suite cost can be compared directly.

```py
from bugbug import perf

costs = perf.load_task_costs()
prices = perf.build_label_prices()
for push in perf.get_perf_pushes():
    actual = perf.summarize_push_cost(push, costs)
    everything = perf.estimate_labels_cost((job["label"] for job in push["perf_jobs"]), prices)
    print(push["revision"], actual["machine_seconds_by_pool"], everything["machine_seconds_by_pool"])
```

## Running the pipeline

### Locally

The script needs the bugbug virtualenv, `REDASH_API_KEY` in the environment
and, for `export`, a Mercurial clone of mozilla-central or mozilla-unified.
Run it from a scratch directory: every dataset is written to `./data/` under
the current directory.

```sh
export REDASH_API_KEY=...
bugbug-data-perf-alerts retrieve --months 1
bugbug-data-perf-alerts generate --settle-days 7
bugbug-data-perf-alerts export --settle-days 7 --repo-dir ~/repos/mozilla-unified
```

Each command first tries to download the previous snapshot from the
Taskcluster index and falls back to the files in `./data/`, logging a warning
until the pipeline has published once. `retrieve` takes `--skip-alerts`,
`--skip-pushes`, `--skip-bugs` and `--skip-costs` so one part can be rerun
alone. The short windows above finish in minutes; the production defaults
issue several hundred Redash queries and hold a year of pushes in memory. Once
the pipeline has run in production, `generate` and `export` work locally from
the published snapshots without running `retrieve`.

### On Taskcluster

Bugbug's data pipeline is the Taskcluster hook `project-bugbug/bugbug`, defined
in `infra/taskcluster-hook-data-pipeline.json`. It fires on the 1st and 16th of
each month and at the end of every deploy, and runs `infra/spawn_pipeline.py`
over `infra/data-pipeline.yml`, which creates every task in that file with
`${version}` set to the deployed bugbug version. The perf tasks are part of
that graph, so running them in production takes no wiring beyond:

1. Landing the change on master.
2. A bugbug release, which builds the Docker images for the new version tag
   and deploys them; the deploy fires the hook. The `bugbug-base` image gets
   the `bugbug-data-perf-alerts` entry point from `pyproject.toml`.
3. A `REDASH_API_KEY` entry in the Taskcluster secret
   `project/bugbug/production`, ideally the key of a dedicated STMO user. The
   retrieval task reads it through the Taskcluster proxy.

The hook can also be triggered by hand from the Taskcluster UI, which runs the
whole bugbug data pipeline since `spawn_pipeline.py` cannot launch a subset.
Failures email `bugbug-team@mozilla.com`. Successful runs publish under the
index routes below, after which `db.download()` fetches the datasets anywhere.

On the first run the alerts snapshot can only map the last four months of
alerts to task labels and the pushes older than that have no jobs, both
because Treeherder deletes jobs after 120 days; later runs keep what earlier
ones captured, see Sources.

## Pipeline tasks

`infra/data-pipeline.yml` runs `perf-alerts-retrieval` (compute-small, needs
the secret), then `perf-regressions-generator` (compute-large), then
`perf-regression-commits-export`, which runs in the `bugbug-commit-retrieval`
image and reuses the Mercurial clone cache of `commit-retrieval`. Artifacts
are indexed under `project.bugbug.data_perf_alerts`,
`project.bugbug.data_perf_regressions` and
`project.bugbug.data_perf_regression_commits`. The tasks pass no flags, so the
`DEFAULT_*` constants in `scripts/perf_alerts_retriever.py` are the production
configuration.
