# Performance Test Scheduling Simulation

Autoland runs most performance tests only on backstop pushes, every 20 pushes
or 4 hours. Bug 2055854 evaluates replacements through shadow schedulers in
Firefox's task graph: fixed-interval batching, time-based batching, and
risk-aware batching driven by bugbug's perf regression predictor. A shadow
scheduler only shows what a strategy would have scheduled on the pushes since
it landed, so comparing strategies that way takes months per parameter value.

The simulator in `bugbug/perf/simulation/` replays the same strategies over
the history held by the [performance alerts data pipeline](perf-alerts.md):
every autoland push, every confirmed regression pinned to its culprit push,
and the risk score of every commit. It measures what each strategy would have
run and how long each regression would have waited, tunes strategy parameters
with optuna on an older part of the history, and evaluates the result on a
newer part. It is driven by `bugbug-perf-scheduling-simulator`, defined in
`scripts/perf_scheduling_simulator.py`.

## Model

### Timeline

`Timeline.from_records` (`timeline.py`) builds the history from three
datasets:

- `perf_pushes.json`: the pushes in time order, each with its `pushlog_id`,
  its commits, whether any commit message carries `DONTBUILD`, and the
  normalized labels of the perf tasks that ran on it, filtered by the same
  exclusions as the regression labels.
- `perf_regressions.json`: summaries with a positive outcome, each becoming a
  `Regression` at its culprit push with the labels of its regressed
  runnables. A summary none of whose alerts maps to a task has no labels and
  is taken to be covered by any full run of the suite.
- `perf_commit_risk.json`, for the risk-aware strategies: `risk_score` and
  `ignored_reason` per commit, from one model version at a time. The scores
  are the model's raw output; `Timeline.with_calibration` maps them through a
  `bugbug.perf.calibration.Calibration`, as `--calibration` asks.

The task graph itself is not archived, so the set of perf tasks that exist at
a given time, the _suite_, is the set of labels that ran on the nearest push
where the whole suite ran, that is a push with at least
`FULL_SUITE_MIN_LABELS` (300) distinct perf labels. Sheriff backfills never
come close to that count, so this picks out backstop pushes.

### Strategies

`strategies.py` reimplements the decision arithmetic of
`taskcluster/gecko_taskgraph/optimize/perf_batching.py` and
`util/backstop.py` in Firefox, without the state machinery that the shadow
schedulers need to hand decisions from one task to the next: the replay sees
the whole history in order. A strategy `observe`s every push and `decide`s
what to run on it: `ALL` for the whole suite, a set of labels, or nothing.

| Family                          | A batch is due when                                                                                                                     |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `production_recorded`           | the whole suite ran on the push in history, and `expanded_labels` where they ran; sheriff backfills are left out (the default baseline) |
| `production`                    | 20 pushes or 4 hours since the last backstop, the rule replayed; `expanded_labels` also run on every 10th pushlog id                    |
| `fixed_interval`                | `push_interval` pushes have been made since the last batch, counted by pushlog id like the backstop                                     |
| `commit_count`                  | `commits` commits the predictor would score have landed since the last batch: RASB with every score equal                               |
| `time_since_batch`              | `hours` have passed since the last batch                                                                                                |
| `rasb`, `rasb_la`               | the risk of the commits since the last batch reaches `threshold`: the probability at least one regressed, or their sum                  |
| `rapb`, `rapb_la`               | as above with each commit's score aged by `aging_rate` per hour waited                                                                  |
| `rapb_pushes`, `rapb_la_pushes` | as above, aged by `aging_rate` per push landed after the commit instead of per hour                                                     |

`PRESETS` holds the shadow schedulers' current parameters, keyed by their
names (`perf_rasb`, ...), and `parse_strategy` reads `family:key=value,...`.
The in-tree code counts pushes by pushlog id, so strategies do too; without
pushlog ids they fall back to the push's position in the timeline, with a
warning.

The baseline's expanded labels, the tier 1 raptor tests that production runs
on every 10th push besides backstops, are derived from history: the labels
that ran on at least half of the non-backstop pushes whose pushlog id is a
multiple of 10, counted from the first full-suite push on. Treeherder drops
jobs after 120 days, so pushes archived later than that show none, and
counting them would argue against every label.

### DONTBUILD pushes

The decision task strips every task from a push whose commit message carries
`DONTBUILD`, so no shadow scheduler runs on it either. The replay observes
such a push, so its commits count towards the next risk decision and its
position towards push intervals, but takes no decision on it and runs
nothing: a batch that would have been due there waits for the next push. The
shadow schedulers' state replay (`SkipUnlessBatchDue`) behaves the same way
since the DONTBUILD fix of bug 2055854.

### Detection

A run of a test catches every regression of that test since the previous run
of it. `detect` (`detection.py`) therefore finds, for each regression, the
first push at or after the culprit where the schedule runs one of its labels.
A batch runs the suite as it stood at its push, so it counts only for the
labels of that suite; before the first full-suite push, where the suite is
only borrowed from it, a batch is credited with any label an alert names,
since the alert proves the label ran and Treeherder has dropped the jobs
that would show it. Two moments follow from it:

- **detect**: that push. `detect_hours` is the time from the culprit push to
  it, `detect_pushes` the pushes in between, and `window_pushes` the pushes
  from the previous run of the label to it, which is what a sheriff has to
  bisect.
- **surface**: Perfherder only raises an alert once `FORE_WINDOW` (12) values
  follow the change, so the alert surfaces at the 12th run of the label
  counted from the detecting one. `surface_hours` is the time to that push.
  The delay is about eleven run intervals, so it grows with sparsity and
  largely restates cost: while alerting waits for later batches, a sparser
  schedule surfaces later by construction. It is always reported, since it
  is what sheriffs see today, but `detect` is the default objective.

Detection uses the whole timeline: a regression near its end may be detected
but not yet surfaced, or not detected at all when no later run exists.

### Metrics and constraint

`evaluate` (`metrics.py`) measures a schedule on a `Window` of pushes: the
batches and jobs run in it (a batch costs the size of the suite at that
time), the pushes and hours between batches, and over the regressions whose
culprit landed in it, how many were detected and surfaced and the mean,
median, 90th percentile and maximum of both latencies, in hours and in
pushes. `merged` counts regressions of one label caught by the same run,
which Perfherder reports as one alert. `--bootstrap N` adds a 95 percent
interval of the mean latencies.

`is_feasible` requires a strategy to have the latency being judged, the
`--latency` objective, for every regression the production baseline has it
for in the window. Catching everything is not enough: a regression caught
near the end of the pushes may not have surfaced or been identified yet, and
a sparser schedule lacks such later events for more regressions, so judging
it on the ones it has would compare fewer, earlier regressions against
production's. Each latency summary carries the ids of the regressions it is
taken over. The baseline is the recorded schedule by
default, since production's backstop decisions also depend on which decision
task finished first and so are not fully reproducible from the rule;
`--baseline rule` replays the rule instead, for history without full-suite
pushes or to check the rule against the record. Treeherder drops jobs after
120 days, so pushes the pipeline archived later than that carry no jobs and
no recorded schedule; the simulator warns when the recorded baseline has a
gap of more than a day, and the rule is the baseline to use over such
history, for both windows, so that they stay comparable. Detection rate is a hard constraint, not an
objective: a strategy that runs nothing has zero cost and no latency to
measure, and would otherwise win.

### CI capacity

Detection above is stamped at the push time of the catching run, as if
results appeared the moment a batch is scheduled. A batch is a burst: the
whole suite lands at once on pools that are mostly fixed hardware, so results
arrive as workers free up, and a sheriff then backfills the pushes in
between to find the culprit on the same pools. `capacity.py` models that.

`CapacityModel` holds, per worker pool, the distinct workers it used each
month, the observed queue waits and run times of autoland's perf runs, and
the hourly occupancy of the pool by everything that is not autoland perf
work, which a strategy replaces; per label, the pool it runs on and its
median run time. `fetch-capacity` builds it from the fxci dataset in
BigQuery through the STMO data source, with the `perf_capacity_*` templates
of `bugbug/perf/sql` and the perf task predicate the cost query shares, over
the span of the pushes to be replayed, keeping the pools that carry at least `--min-share` of autoland
perf runs, and records the Taskcluster queue's current worker listing for
reference. Labels the model has not seen take the pool their platform's
other labels use and that pool's typical run time.

`simulate_ci` replays a schedule through one FIFO queue per pool: every run
of a batch becomes eligible a build time after its push, the pool's measured
median from the decision task to its perf tests becoming pending, or
`--build-minutes` for a pool without one; it takes a worker when one of the
pool's workers is free of other work, and completes after the label's run
time. A regression is detected at the first completion among its regressed
labels' catching runs, or at the median completion of the detecting batch
when the regression names no label. The label whose run is scheduled first
need not complete first, since labels queue on different pools, so the
replay waits for every label of the regression and the alert and backfills
follow the one that completed.

The alert that puts a sheriff to work comes later, and `--alerting` says
when. `fore_window`, the default, is Perfherder today: the alert needs
`FORE_WINDOW` (12) runs after the culprit, so it is raised when the 12th
run completes. `retrigger` models a proposed change: the first data point
is confirmed by `--confirm-retriggers` (12) more runs of the detecting test
on the same push, and the alert is raised when they complete; every
suspicion is assumed right, so the retriggers are only spent on real
regressions. `detection` raises the alert at the first data point, an upper
bound on how fast anyone could react. At the alert, with `--backfill all`,
the regressed labels are submitted on every push between the previous run
and the detecting one that has no data, is not DONTBUILD and that no earlier
alert has already requested, whose results every alert shares, after
`--sheriff-delay-minutes`. Autoland builds a push only when something
scheduled on it needs the build. A backfill on a push the schedule ran
nothing on therefore finds a build only if other tests on the push needed
one, which the replay cannot see; `fetch-capacity` measures, per pool, the
share of pushes without a full perf suite that carry the pool's shippable
build, and `--backfill-builds measured` (the default) decides each such push
by a stable draw against that share, so the same push gets the same answer
under every strategy. Without a build the backfill builds first and waits
the pool's eligible time; `--backfill-builds always` charges every such
backfill the build, a pessimistic bound. The culprit
counts as identified when the culprit push and the push before it both have
data for one regressed label, and no earlier than the sheriff turns to the
alert, `--sheriff-delay-minutes` after it: data a dense schedule produced
before the alert identifies nothing until someone looks. The pushes dataset
shows sheriffs do backfill nearly the whole window, so that is the default;
`--backfill none` leaves them out.

With `--capacity PATH` on `simulate` or `optimize`, `evaluate` adds
`detect_ci_hours`, `surface_ci_hours`, `identify_hours`, `backfill_jobs`,
`confirm_jobs` and `total_jobs` (the schedule's jobs plus backfills and
retriggers), and `--latency detect_ci`, `--latency surface_ci`,
`--latency identify` and `--cost total_jobs` optimize on them.

The model is optimistic on its own: workers seen in a month include machines
down part of it, and hourly background averages smooth the bursts other
projects cause. `validate-capacity` replays production through the model and
prints, per pool, the observed and replayed wait quantiles, the share of the
window the schedule's runs occupy and the recorded backfill behavior;
`--fit-scales` first fits each pool's effective share of workers so the
replayed median wait matches the observed one, saving the scales into the
capacity file. Tail waits stay under the observed ones even then, since the
bursts behind them are not modeled; comparisons between strategies, which all
go through the same queues, are the intended use.

### Windows and optimization

`make_windows` (`splits.py`) cuts the history chronologically at
`--optimize-until`: parameters are tuned on the older window and judged on
the newer one, since regressions cluster in time and stateful strategies
carry state across pushes. Both windows start after `--warmup-pushes` (50) so
a cold-start batch never counts, and default to the span of the regressions,
from the first culprit push to just after the last one; the pipeline's settle
delay already bounds that. `--since` also starts the replayed history there,
dropping earlier pushes, so a risk family only needs scores from that date
on, and stateful strategies cold-start at the first kept push.

`optimize_family` (`optimize.py`) runs one optuna study per family on the
optimize window with two objectives to minimize, cost (`jobs` by default,
which charges a batch the size of the suite at the time and counts
production's expanded runs too; `batches` counts full-suite runs only)
and the latency, `detect` by default since it is what a schedule
controls, `surface` with `--latency surface`, or counted in pushes rather
than hours: `detect_pushes`, from the culprit to the detecting run, which
quiet hours do not inflate, or `window_pushes`, from the previous run to the
detecting one, what a sheriff bisects. It is summarized by `--statistic`:
the `mean` over the window's regressions by default, or `median`, `p90` or
`max`; the maximum rests on a single regression per window, so it is the
noisiest choice and the one least likely to transfer. Trials that fail the
constraint on that latency are pruned. Each family declares its parameters' bounds in `search_space`
(`strategies.py`); `search_space_for` can narrow them from the data, which
the linear-aggregation families do for their threshold, a sum of scores
whose scale depends on the model and on calibration: it runs from the 1st
percentile of a single commit's score to the 99th percentile of the score
accumulated over 48 hours of pushes. A one-parameter family is swept over
every value, since its trade-off is a single curve: every integer push
interval from 2 to 200, and `--trials` (400) values spaced evenly, or
evenly in log, across a real parameter's bounds, so hours and thresholds are
tried at about one percent resolution. The two-parameter families use
NSGA-II for `--trials` trials with a population of 20, so a run holds many
generations of actual evolution rather than one random generation. Each
trial takes well under a second on the full history. The space each study
used is in the report. A pruned trial of a batching family only means the
strategy went longer without a batch than the pushes after the regression
allow; the report lists pruned trials with the regressions they missed.
Every point of the Pareto front is then re-evaluated on the eval window.
`matching_points` answers the two usual questions against production, the
fastest point that costs no more and the cheapest point that is no slower,
using the optimize window's numbers for both the points and the baseline, so
the eval window only judges choices already made and never influences them.
The report shows the eval results of those two picks per family and keeps the
whole front, with both windows' numbers, in its JSON output.

## Running

The datasets are read from `./data` under the current directory and
downloaded from the Taskcluster index when absent, like the pipeline commands.
Install the `perf-simulation` extra for optuna.

```sh
cd ~/perf-alerts-sample
uv run --extra perf-simulation bugbug-perf-scheduling-simulator simulate \
  --strategy production --strategy preset:perf_fixed_batch \
  --strategy time_since_batch:hours=4 --output simulate.json
uv run --extra perf-simulation bugbug-perf-scheduling-simulator optimize \
  --family fixed_interval --family time_since_batch \
  --optimize-until 2026-09-01 --trials 60 --output optimize.json --markdown optimize.md
```

`simulate` prints one row per strategy with its cost, detection counts, both
latencies as mean / median / p90 / max hours, the mean bisection window and
whether it is feasible against production. `optimize` prints the baseline and,
per family, the two picks with their eval results, plus any pruned trials.
The full fronts, with both windows' numbers, go to the JSON and markdown
files as a diagnostics section.

The risk families' thresholds apply to the scores as the strategies see them,
which `--calibration` chooses. `fit`, the default, fits the pipeline's
isotonic mapping from raw scores to culprit rates on labeled scored commits
from before the window being judged and applies it to every commit, so the
thresholds are probabilities of a regression and the replay sees the scores
as the HTTP service would have served them, from a table fitted on the past.
`optimize` fits on the optimize window and applies that table to both
windows; `simulate` fits on the history before its window, using the
datasets' labels rather than only the pushes kept by `--since`, so a window
that starts at the first regression has nothing to fit on and needs
`--since` or another mode. `published` applies the pipeline's
`perf_risk_calibration.json` for the model version, which was fitted on all
settled history including the window, so it flatters a replay slightly but
shows what the deployed table does. `none` keeps the model's raw output, and
a path applies calibration records from a file.

The risk families need a score for every commit pushed before the end of the
window, `--until` or the default bound. The pipeline scores commits two days
behind the pushes it archives, so the newest pushes carry no scores: the
history is cut at the first unscored push, keeping the scored pushes after
the window as the follow-up data that detections near its end need, and the
simulator refuses to run a risk family when a commit inside the window has no
score. Until `perf_commit_risk.json` has been published, pass
`--risk-scores PATH` with a JSON lines file of the same shape. A synthetic
fixture only exercises the plumbing, not the strategies' merit, but it is
enough to develop against:

```python
import hashlib, json
from bugbug.perf import datasets

with open("risk_fixture.jsonl", "w") as out:
    for push in datasets.get_perf_pushes():
        for commit in push["commits"]:
            node = commit["revision"]
            ignored = "ignore-this-changeset" in (commit["desc"] or "")
            digest = int(hashlib.sha1(node.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
            out.write(json.dumps({
                "node": node, "push_id": push["push_id"],
                "risk_score": None if ignored else 0.01 + 0.4 * digest**3,
                "ignored_reason": "ignore-this-changeset" if ignored else None,
                "model_version": "fixture-v0",
            }) + "\n")
```

## Fidelity

`tests/test_perf_simulation.py` pins the reimplementation to the in-tree code
with `tests/fixtures/perf_simulation_replay.json`: a replay of the real
`gecko_taskgraph` strategy classes, with the parameters they ran with on
2026-10-20, recorded in the fixture (the presets have since been tuned again),
over 420 autoland pushes (283904 to 284323) and bugbug's recorded risk
predictions, plus the backstop flags production actually recorded for those
pushes. The simulator must reproduce every batch of every strategy, and its
production rule must reproduce the recorded backstops. Production's chain is
not fully deterministic: decision tasks do not always finish in push order,
and when a later push's task indexes itself as the latest backstop first, the
earlier push sees a negative distance and is not a backstop, shifting the
chain by one push until a time-triggered backstop resynchronizes it. Three of
the 28 recorded backstops are shifted that way, so the test requires exact
agreement from the first time-triggered backstop on.

## Approximations

- The suite comes from the nearest full-suite push, so tasks added or removed
  between two backstops are seen a few pushes late, and before the first
  full-suite push it is borrowed from a push months later: the cost of a
  batch there is that push's suite, and a regressed label it lacks is taken
  to have run in every batch.
- Production also runs a few tests on nearly every push through bugbug's test
  selection; the baseline does not model them, since the strategies under
  evaluation replace every perf cadence at once.
- Cost is job counts. Machine time per worker pool is available through
  `task_costs.py` and could replace it.
- The CI capacity model has one FIFO queue per pool with hourly background
  occupancy, a median build time per pool, no retriggers for noise and no
  sheriff reaction time; it reproduces median waits once fitted, not the
  tails. Regressions whose alert named no task label cannot be backfilled or
  retriggered, so they count as identified when the sheriff turns to their
  alert.
- Perfherder's alert threshold and the merging of nearby changes are not
  modeled; surfacing assumes one value per run per signature and no
  retriggers.
