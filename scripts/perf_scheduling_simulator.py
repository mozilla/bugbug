# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Replay performance test scheduling strategies over autoland history.

``simulate`` replays named strategies and the production baseline over a
window of the perf pushes snapshot and reports what each ran and how fast it
would have caught every confirmed regression. ``optimize`` tunes each strategy
family with optuna on an older window, keeping only parameters that catch
everything production caught, and evaluates the resulting Pareto front on a
newer window. Both read the datasets of the performance alerts pipeline from
``./data`` and download them from the Taskcluster index when absent.
"""

import argparse
import bisect
import json
import logging
import statistics
from datetime import datetime, timezone
from typing import Any

from bugbug import redash
from bugbug.perf import datasets, extraction, push_status
from bugbug.perf import simulation as sim
from bugbug.perf.calibration import (
    DEFAULT_MIN_POSITIVES,
    fit_calibration,
    label_scored_commits,
    load_calibrations,
)
from bugbug.perf.simulation.splits import DEFAULT_WARMUP_PUSHES
from bugbug.utils import parse_timestamp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_TRIALS = 400


def load_timeline(
    need_risks: bool,
    risk_scores: str | None,
    model_version: str | None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[sim.Timeline, dict[str, tuple[float | None, str | None]] | None, str | None]:
    """Build the timeline from the datasets, with risk scores when a strategy needs them.

    ``risk_scores`` names a JSON lines file shaped like ``perf_commit_risk.json``
    to use instead of the published dataset, such as a fixture. With ``since``
    the history starts there: earlier pushes are dropped, so stateful
    strategies cold-start at the first kept push, which the warm-up excludes.
    With risk scores, every commit pushed before ``until`` has to be scored
    and the history ends at the first unscored push; see ``scored_history``.
    Returns the timeline, the raw scores by node and their model version.
    """
    datasets.ensure_dataset(datasets.PERF_PUSHES_DB)
    datasets.ensure_dataset(datasets.PERF_REGRESSIONS_DB)
    risks = None
    version = None
    if need_risks:
        if risk_scores:
            with open(risk_scores, encoding="utf-8") as fh:
                records: Any = [json.loads(line) for line in fh if line.strip()]
        else:
            datasets.ensure_dataset(datasets.PERF_COMMIT_RISK_DB)
            records = datasets.get_perf_commit_risk()
        risks, version = sim.load_risks(records, model_version)
        logger.info("Risk scores of %d commits from model %s", len(risks), version)
    pushes: Any = datasets.get_perf_pushes()
    if since is not None:
        pushes = (
            push
            for push in pushes
            if (time := parse_timestamp(push["time"])) is not None and time >= since
        )
    timeline = sim.Timeline.from_records(pushes, datasets.get_perf_regressions(), risks)
    if need_risks:
        timeline = scored_history(timeline, until)
    return timeline, risks, version


def scored_history(timeline: sim.Timeline, until: datetime | None) -> sim.Timeline:
    """The timeline up to its first push with an unscored commit.

    The pipeline scores commits two days behind the pushes it archives, so the
    newest pushes carry no scores. Every commit pushed before the horizon,
    ``until`` or the default end of the window, has to be scored; the scored
    pushes after it are kept as the follow-up history that detections near
    the horizon need.
    """
    horizon = until or sim.default_bounds(timeline)[1]
    unscored = [
        push
        for push in timeline.pushes
        if any(commit.scored and commit.risk is None for commit in push.commits)
    ]
    if not unscored:
        return timeline
    first = unscored[0]
    if first.time < horizon:
        commits = sum(
            1
            for push in unscored
            if push.time < horizon
            for commit in push.commits
            if commit.scored and commit.risk is None
        )
        raise SystemExit(
            f"{commits} commits of pushes before {horizon} have no risk score; the "
            "risk strategies need every commit scored. Run the commit risk "
            "scorer over the window first, or narrow --since/--until."
        )
    logger.warning(
        "Dropping the %d pushes from %s on, whose commits have no risk score yet; "
        "%d scored pushes after %s remain as follow-up history",
        len(timeline.pushes) - first.index,
        first.time,
        sum(1 for push in timeline.pushes[: first.index] if push.time >= horizon),
        horizon,
    )
    return timeline.truncate(first.index)


def calibrate_timeline(
    timeline: sim.Timeline,
    mode: str,
    risks: dict[str, tuple[float | None, str | None]] | None,
    version: str | None,
    window: sim.Window,
    fit_on: str = "within",
) -> tuple[sim.Timeline, dict[str, Any] | None]:
    """Give the risk strategies the scores as ``mode`` says; returns a summary too.

    ``none`` keeps the model's raw scores. ``fit`` fits the pipeline's
    isotonic mapping to culprit rates on labeled scored commits and applies it
    to every commit: ``fit_on`` ``within`` takes the commits inside ``window``,
    for an optimize window whose eval window follows, and ``before`` the ones
    pushed before it, so a replayed window sees a table fitted on its past as
    the endpoint's would be. ``published`` applies the pipeline's calibration
    of the model version; any other value names a JSON lines file of such
    records.
    """
    if mode == "none" or risks is None:
        return timeline, None
    summary: dict[str, Any]
    if mode == "fit":
        datasets.ensure_dataset(datasets.PERF_PUSH_STATUS_DB)
        scores = {
            node: score for node, (score, _) in risks.items() if score is not None
        }
        rows = label_scored_commits(
            datasets.get_perf_regressions(), push_status.read_push_status(), scores
        )
        if fit_on == "before":
            rows = [row for row in rows if row.time < window.start]
            span = f"before {window.start}"
        else:
            rows = [row for row in rows if window.contains(row.time)]
            span = f"between {window.start} and {window.end}"
        positives = sum(row.label for row in rows)
        if positives < DEFAULT_MIN_POSITIVES:
            raise SystemExit(
                f"{positives} scored culprits {span}, at least "
                f"{DEFAULT_MIN_POSITIVES} are needed to fit a calibration. Start the "
                "window later (simulate) or widen the optimize window, or pass "
                "--calibration published or none."
            )
        calibration = fit_calibration(
            [row.score for row in rows], [row.label for row in rows], version or ""
        )
        summary = {
            "mode": mode,
            "fitted_on": fit_on,
            "since": rows[0].time.isoformat(),
            "until": rows[-1].time.isoformat(),
            "commits": len(rows),
            "positives": positives,
        }
    else:
        if mode == "published":
            datasets.ensure_dataset(datasets.PERF_RISK_CALIBRATION_DB)
            records: Any = datasets.get_perf_risk_calibration()
        else:
            with open(mode, encoding="utf-8") as fh:
                records = [json.loads(line) for line in fh if line.strip()]
        calibrations = load_calibrations(records)
        if version not in calibrations:
            raise SystemExit(
                f"No calibration for model version {version}; the records hold "
                f"{sorted(calibrations)}"
            )
        calibration = calibrations[version]
        summary = {"mode": mode, "fitted_at": calibration.fitted_at}
    summary["breakpoints"] = len(calibration.x)
    summary["max_probability"] = max(calibration.y)
    logger.info(
        "Risk scores calibrated (%s): %d breakpoints, highest probability %.3f",
        mode,
        len(calibration.x),
        max(calibration.y),
    )
    return timeline.with_calibration(calibration), summary


BASELINES = {"recorded": sim.RecordedProduction, "rule": sim.Production}
TUNABLE_FAMILIES = [
    f for f in sim.FAMILIES if f not in ("production", "production_recorded")
]


def production(timeline: sim.Timeline, baseline: str) -> sim.Strategy:
    """The production baseline: the recorded schedule, or the backstop rule replayed."""
    expanded = timeline.expanded_labels()
    logger.info(
        "Production baseline (%s): %d labels on expanded pushes",
        baseline,
        len(expanded),
    )
    if baseline == "recorded":
        full = [push for push in timeline.pushes if push.full_suite]
        gaps = [
            (b.time - a.time).total_seconds() / 3600 for a, b in zip(full, full[1:])
        ]
        longest = max(gaps, default=float("inf"))
        if longest > 24:
            logger.warning(
                "The recorded schedule has no full-suite push for up to %.0f hours. "
                "Treeherder drops jobs after 120 days, so pushes archived later than "
                "that have none; use --baseline rule over such history.",
                longest,
            )
    return BASELINES[baseline](expanded_labels=expanded)


def resolve_strategy(spec: str, timeline: sim.Timeline, baseline: str) -> sim.Strategy:
    if spec == "production":
        return production(timeline, baseline)
    return sim.parse_strategy(spec)


def _stats(summary: dict[str, Any] | None) -> str:
    if summary is None or summary["mean"] is None:
        return "-"
    return "{mean:.1f} / {median:.1f} / {p90:.1f} / {max:.1f}".format(**summary)


def format_table(rows: list[tuple[str, dict[str, Any], bool]]) -> str:
    """A markdown table of per-strategy results; latencies are mean / median / p90 / max.

    The CI columns are filled when the strategies were replayed through the
    capacity model (``--capacity``).
    """
    header = (
        "| strategy | batches | jobs | regressions | detected "
        "| detect hours | detect pushes | window pushes (mean) "
        "| merged | ci detect hours | identify hours | backfill jobs | feasible |"
    )
    lines = [header, "|" + "---|" * 13]
    for name, result, feasible in rows:
        lines.append(
            f"| {name} | {result['batches']} | {result['jobs']} | "
            f"{result['regressions']} | {result['detected']} | "
            f"{_stats(result['detect_hours'])} | "
            f"{_stats(result['detect_pushes'])} | "
            f"{(result['window_pushes']['mean'] or 0):.1f} | {result['merged']} | "
            f"{_stats(result.get('detect_ci_hours'))} | "
            f"{_stats(result.get('identify_hours'))} | "
            f"{result.get('backfill_jobs', '-')} | "
            f"{'yes' if feasible else 'NO'} |"
        )
    return "\n".join(lines)


def ci_settings(args: argparse.Namespace) -> sim.CiSettings | None:
    """The CI replay settings from ``--capacity`` and its companions, or None."""
    if not getattr(args, "capacity", None):
        if args.latency in ("detect_ci", "identify") or (
            getattr(args, "cost", "jobs") == "total_jobs"
        ):
            raise SystemExit(
                f"--latency {args.latency} / --cost total_jobs need --capacity"
            )
        return None
    model = sim.CapacityModel.load(args.capacity)
    logger.info(
        "CI capacity model: %d pools, %d labels, build fallback %.0f min, backfill %s",
        len(model.pools),
        len(model.labels),
        args.build_minutes,
        args.backfill,
    )
    return sim.CiSettings(
        model,
        args.build_minutes,
        args.backfill,
        args.sheriff_delay_minutes,
        args.capacity_scale,
        record_waits=args.op == "validate-capacity",
        backfill_builds=args.backfill_builds,
    )


def cmd_simulate(args: argparse.Namespace) -> None:
    specs = args.strategy or ["production", *(f"preset:{p}" for p in sim.PRESETS)]
    need_risks = any(
        sim.parse_strategy(spec).family in sim.RISK_FAMILIES
        for spec in specs
        if spec != "production"
    )
    timeline, risks, version = load_timeline(
        need_risks, args.risk_scores, args.model_version, args.since, args.until
    )
    since, until = sim.default_bounds(timeline)
    window = sim.Window(args.since or since, args.until or until)
    if args.warmup_pushes and len(timeline.pushes) > args.warmup_pushes:
        window = sim.Window(
            max(window.start, timeline.pushes[args.warmup_pushes].time), window.end
        )
    logger.info("Window: %s to %s", window.start, window.end)
    timeline, calibration = calibrate_timeline(
        timeline, args.calibration or "fit", risks, version, window, fit_on="before"
    )
    ci = ci_settings(args)

    baseline = sim.run(
        production(timeline, args.baseline),
        timeline,
        [window],
        args.bootstrap,
        ci,
    )[0]
    rows: list[tuple[str, dict[str, Any], bool]] = [("production", baseline, True)]
    for spec in specs:
        if spec == "production":
            continue
        strategy = resolve_strategy(spec, timeline, args.baseline)
        result = sim.run(strategy, timeline, [window], args.bootstrap, ci)[0]
        rows.append(
            (strategy.name, result, sim.is_feasible(result, baseline, args.latency))
        )

    print(format_table(rows))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "window": rows[0][1]["window"],
                    "calibration": calibration,
                    "results": [
                        {"name": name, "feasible": feasible, "metrics": result}
                        for name, result, feasible in rows
                    ],
                },
                fh,
                indent=2,
            )
        logger.info("Wrote %s", args.output)


DIAGNOSTICS_NOTE = (
    "Every point of each family's Pareto front, replayed on the eval window to "
    "show whether the trade-off curve transfers between periods. Choosing a "
    "point from these eval numbers would be selecting on the eval data; the "
    "picks in the summary were made on the optimize window."
)


def _objective_row(
    family: str,
    question: str,
    row: dict[str, Any] | None,
    cost: str,
    latency: str,
    statistic: str,
) -> str:
    if row is None:
        return f"| {family} | {question} | - | - | - | none on the front |"
    metrics = row["eval"]["metrics"]
    return (
        f"| {family} | {question} | {row['name']} | {metrics[cost]} | "
        f"{metrics[sim.latency_key(latency)][statistic]:.1f} | "
        f"{metrics['detected']} of {metrics['regressions']} |"
    )


def _front_rows(family: str, rows: list[dict[str, Any]]) -> list[str]:
    return [
        f"| {family} | {row['name']} | {row['optimize']['objectives'][0]:.0f} | "
        f"{row['optimize']['objectives'][1]:.1f} | {row['eval']['objectives'][0]:.0f} | "
        f"{row['eval']['objectives'][1]:.1f} |"
        for row in rows
    ]


def cmd_optimize(args: argparse.Namespace) -> None:
    families = args.family or TUNABLE_FAMILIES
    need_risks = any(family in sim.RISK_FAMILIES for family in families)
    timeline, risks, version = load_timeline(
        need_risks, args.risk_scores, args.model_version, args.since, args.until
    )
    optimize_window, eval_window = sim.make_windows(
        timeline, args.optimize_until, args.since, args.until, args.warmup_pushes
    )
    logger.info(
        "Optimize on %s to %s, evaluate on %s to %s",
        optimize_window.start,
        optimize_window.end,
        eval_window.start,
        eval_window.end,
    )
    timeline, calibration = calibrate_timeline(
        timeline, args.calibration or "fit", risks, version, optimize_window
    )
    ci = ci_settings(args)
    baseline_optimize, baseline_eval = sim.run(
        production(timeline, args.baseline),
        timeline,
        [optimize_window, eval_window],
        args.bootstrap,
        ci,
    )
    baseline_objectives = sim.objectives(
        baseline_optimize, args.cost, args.latency, args.statistic
    )
    eval_objectives = sim.objectives(
        baseline_eval, args.cost, args.latency, args.statistic
    )

    report: dict[str, Any] = {
        "settings": {
            "cost": args.cost,
            "latency": args.latency,
            "statistic": args.statistic,
            "trials": args.trials,
            "seed": args.seed,
            "baseline": args.baseline,
            "calibration": calibration,
            "ci": (
                {
                    "capacity": args.capacity,
                    "build_minutes": args.build_minutes,
                    "backfill": args.backfill,
                    "sheriff_delay_minutes": args.sheriff_delay_minutes,
                    "capacity_scale": args.capacity_scale,
                    "backfill_builds": args.backfill_builds,
                }
                if ci
                else None
            ),
            "optimize_window": baseline_optimize["window"],
            "eval_window": baseline_eval["window"],
        },
        "baseline": {"optimize": baseline_optimize, "eval": baseline_eval},
        "families": {},
    }
    label = sim.latency_label(args.latency)
    headline = [
        f"Baseline (production): {args.cost} {baseline_objectives[0]:.0f} and "
        f"{args.statistic} {label} {baseline_objectives[1]:.1f} on the "
        f"optimize window; {args.cost} {eval_objectives[0]:.0f} and {args.statistic} "
        f"{label} {eval_objectives[1]:.1f} on the eval window, "
        f"{baseline_eval['detected']} of "
        f"{baseline_eval['regressions']} regressions detected.",
        "Picks are made on the optimize window; the columns show their eval window results.",
        (
            f"Risk scores: {calibration['mode']} calibration, highest probability "
            f"{calibration['max_probability']:.3f}."
            if calibration
            else "Risk scores: the model's raw output."
        ),
        "",
        f"| family | question | strategy | {args.cost} | {args.statistic} {label} | detected |",
        "|---|---|---|---|---|---|",
    ]
    notes = []
    diagnostics = [
        f"| family | strategy | optimize {args.cost} | optimize {args.statistic} "
        f"{label} | eval {args.cost} | eval {args.statistic} {label} |",
        "|---|---|---|---|---|---|",
    ]
    for family in families:
        study = sim.optimize_family(
            family,
            timeline,
            optimize_window,
            baseline_optimize,
            args.trials,
            args.seed,
            args.cost,
            args.latency,
            args.statistic,
            ci,
            args.max_cost_ratio,
        )
        points = sim.pareto_points(study)
        rows = sim.evaluate_points(
            family,
            points,
            timeline,
            eval_window,
            args.cost,
            args.latency,
            args.bootstrap,
            args.statistic,
            ci,
        )
        picks = sim.matching_points(rows, baseline_objectives)
        pruned = sim.pruned_trials(study)
        report["families"][family] = {
            "trials": len(study.trials),
            "search_space": study.user_attrs["search_space"],
            "pruned": pruned,
            "picks": picks,
            "diagnostics": {"note": DIAGNOSTICS_NOTE, "front": rows},
        }
        headline.append(
            _objective_row(
                family,
                f"at production's {args.cost}",
                picks["at_cost"],
                args.cost,
                args.latency,
                args.statistic,
            )
        )
        headline.append(
            _objective_row(
                family,
                "at production's latency",
                picks["at_latency"],
                args.cost,
                args.latency,
                args.statistic,
            )
        )
        sparse = [trial for trial in pruned if trial["pruned_for"] == "missed"]
        costly = [trial for trial in pruned if trial["pruned_for"] == "cost"]
        if sparse:
            missed = sorted({i for trial in sparse for i in trial["missed_ids"]})
            names = ", ".join(
                sim.FAMILIES[family](**trial["params"]).name for trial in sparse
            )
            notes.append(
                f"{family}: {len(sparse)} trials pruned as too sparse to reach the "
                f"{label} of regressions {missed}, which production reaches: {names}."
            )
        if costly:
            notes.append(
                f"{family}: {len(costly)} trials pruned before the CI replay as "
                f"costing more than {args.max_cost_ratio} times production."
            )
        diagnostics.extend(_front_rows(family, rows))

    summary = "\n".join(headline + ([""] + notes if notes else []))
    print(summary)
    print(
        "\nFull Pareto fronts are kept as diagnostics in the --output and --markdown files."
    )
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        logger.info("Wrote %s", args.output)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as fh:
            fh.write(summary + "\n\n## Diagnostics: full Pareto fronts\n\n")
            fh.write(DIAGNOSTICS_NOTE + "\n\n" + "\n".join(diagnostics) + "\n")
        logger.info("Wrote %s", args.markdown)


def cmd_fetch_capacity(args: argparse.Namespace) -> None:
    client = redash.RedashClient(redash.get_redash_api_key())
    until = args.until or datetime.now(timezone.utc)
    model = sim.fetch_capacity(
        client,
        extraction.BIGQUERY_DATA_SOURCE_ID,
        args.since,
        until,
        args.min_share,
        not args.no_list_workers,
    )
    model.save(args.output)
    print(
        "| pool | workers by month (first, last) | listed now | autoland perf runs/day | observed wait p50/p90 min | run p50/p90 min | background busy (mean) |"
    )
    print("|---|---|---|---|---|---|---|")
    for name, pool in sorted(model.pools.items(), key=lambda kv: -kv[1].runs_per_day):
        months = sorted(pool.workers_by_month)
        busy = model.background.get(name, {})
        print(
            f"| {name} | {pool.workers_by_month.get(months[0]) if months else '-'} ({months[0] if months else '-'}), "
            f"{pool.workers_by_month.get(months[-1]) if months else '-'} ({months[-1] if months else '-'}) | "
            f"{pool.listed_workers} | {pool.runs_per_day:.0f} | "
            f"{pool.observed_wait_minutes.get('p50', 0):.0f} / {pool.observed_wait_minutes.get('p90', 0):.0f} | "
            f"{pool.observed_run_minutes.get('p50', 0):.0f} / {pool.observed_run_minutes.get('p90', 0):.0f} | "
            f"{(sum(busy.values()) / len(busy)) if busy else 0:.1f} |"
        )
    logger.info("Wrote %s: %d labels", args.output, len(model.labels))


def cmd_validate_capacity(args: argparse.Namespace) -> None:
    """Replay production through the model and compare its waits with the observed ones.

    With ``--fit-scales`` each pool's scale is first fitted to the observed
    median wait and written back into the capacity file.
    """
    timeline, _, _ = load_timeline(False, None, None, args.since)
    since, until = sim.default_bounds(timeline)
    window = sim.Window(args.since or since, args.until or until)
    if args.warmup_pushes and len(timeline.pushes) > args.warmup_pushes:
        window = sim.Window(
            max(window.start, timeline.pushes[args.warmup_pushes].time), window.end
        )
    ci = ci_settings(args)
    assert ci is not None
    strategy = production(timeline, args.baseline)
    schedule = sim.replay(strategy, timeline)
    detections = sim.detect(schedule, timeline)
    if args.fit_scales:
        fitted = sim.calibrate_scales(ci.model, schedule, timeline, detections, ci)
        ci.model.save(args.capacity)
        logger.info("Fitted %d pool scales into %s", len(fitted), args.capacity)
    result = sim.simulate_ci(schedule, timeline, detections, ci)
    metrics = sim.evaluate(timeline, schedule, detections, window, 0, result)
    pushes_in_window = [p for p in timeline.pushes if window.contains(p.time)]
    print(
        f"Production ({args.baseline}) on {window.start:%Y-%m-%d} to {window.end:%Y-%m-%d}: "
        f"{metrics['batches']} batches, {metrics['jobs']} jobs, {result.unknown_labels} runs "
        f"of labels with no pool dropped, {metrics['backfill_jobs']} backfill jobs for "
        f"{metrics['regressions']} regressions.\n"
        f"Detect hours at push time {metrics['detect_hours']['mean']:.2f}, at run completion "
        f"{metrics['detect_ci_hours']['mean']:.2f}; identify hours {metrics['identify_hours']['mean']:.2f} "
        f"over {metrics['identified']} identified.\n"
    )
    print(
        "| pool | workers (window end) | scale | eligible min | build share | listed now | observed autoland wait p50/p90 min | simulated wait p50/p90 min (batches) | simulated p50/p90 (backfills) | scheduled runs | utilization by our runs | background busy (mean) |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    end = pushes_in_window[-1].time if pushes_in_window else timeline.end
    for name, pool in sorted(
        ci.model.pools.items(), key=lambda kv: -kv[1].runs_per_day
    ):
        waits = result.waits.get(name, [])
        backfills = result.backfill_waits.get(name, [])
        busy = ci.model.background.get(name, {})
        print(
            f"| {name} | {pool.workers_at(end)} | {pool.scale:.2f} | "
            f"{pool.eligible_minutes if pool.eligible_minutes is None else round(pool.eligible_minutes)} | "
            f"{'-' if pool.build_share is None else f'{100 * pool.build_share:.0f}%'} | {pool.listed_workers} | "
            f"{pool.observed_wait_minutes.get('p50', 0):.0f} / {pool.observed_wait_minutes.get('p90', 0):.0f} | "
            f"{_quantiles(waits)} | {_quantiles(backfills)} | {len(waits)} | {100 * result.utilization[name]:.0f}% | "
            f"{(sum(busy.values()) / len(busy)) if busy else 0:.1f} |"
        )
    fractions = _recorded_backfill_fractions(timeline, window)
    if fractions:
        print(
            f"\nRecorded sheriff backfills on {len(fractions)} labeled regressions: a mean "
            f"{100 * statistics.fmean(fractions):.0f}% of the pushes between the full runs "
            f"around the culprit got the regressed label run; {sum(1 for f in fractions if f > 0)} "
            f"regressions had any."
        )


def _quantiles(values: list[float]) -> str:
    """Median and 90th percentile as ``p50 / p90``, ``-`` without values."""
    if not values:
        return "-"
    return f"{sim.percentile(values, 0.5):.0f} / {sim.percentile(values, 0.9):.0f}"


def _recorded_backfill_fractions(
    timeline: sim.Timeline, window: sim.Window
) -> list[float]:
    """Share of pushes between the recorded full runs around each culprit that ran a regressed label."""
    full = [p.index for p in timeline.pushes if p.full_suite]
    fractions = []
    for regression in timeline.regressions:
        if not regression.labels or not window.contains(regression.time):
            continue
        position = bisect.bisect_left(full, regression.push_index)
        if position == 0 or position >= len(full):
            continue
        previous, detecting = full[position - 1], full[position]
        between = [
            timeline.pushes[i]
            for i in range(previous + 1, detecting)
            if not timeline.pushes[i].dontbuild
        ]
        if not between:
            continue
        ran = sum(1 for p in between if regression.labels & p.ran_labels)
        fractions.append(ran / len(between))
    return fractions


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--capacity",
        metavar="PATH",
        help="CI capacity model written by fetch-capacity. Replays every run through one FIFO queue per worker pool, eligible --build-minutes after the push, then the sheriff's backfills, and adds the detect_ci and identify latencies and the total_jobs cost.",
    )
    parser.add_argument(
        "--build-minutes",
        type=float,
        default=sim.capacity.DEFAULT_BUILD_MINUTES,
        help="Minutes from a push to its tests becoming eligible: the build. Default: %(default)s.",
    )
    parser.add_argument(
        "--backfill",
        choices=["all", "none"],
        default="all",
        help="With --capacity: backfill the regressed labels on every push between the previous run and the catching one once a regression is caught, or not at all. Default: %(default)s.",
    )
    parser.add_argument(
        "--capacity-scale",
        type=float,
        default=1.0,
        help="With --capacity: share of each pool's workers effectively serving, to match the observed queue waits (validate-capacity). Default: %(default)s.",
    )
    parser.add_argument(
        "--backfill-builds",
        choices=["measured", "always"],
        default="measured",
        help="With --capacity: whether a backfill on a push the schedule ran nothing on finds a shippable build. measured (default): with the pool's measured share of such pushes that have one, decided by a stable draw per push. always: never, so every such backfill builds first.",
    )
    parser.add_argument(
        "--sheriff-delay-minutes",
        type=float,
        default=0.0,
        help="With --capacity: minutes between the catching run completing and the sheriff triggering the backfills; identification is never earlier. Default: %(default)s.",
    )
    parser.add_argument(
        "--since",
        type=parse_timestamp,
        metavar="DATE",
        help="Start of the history (ISO 8601, UTC): earlier pushes are dropped, stateful strategies cold-start at the first kept push, and the first window starts at the later of this date and the first regression. Default: the whole pushes snapshot.",
    )
    parser.add_argument(
        "--until",
        type=parse_timestamp,
        metavar="DATE",
        help="End of the history to evaluate. Default: just after the last regression's culprit push. A risk family needs every commit pushed before it scored; the scored pushes after it are kept as follow-up history and the first unscored push ends it.",
    )
    parser.add_argument(
        "--warmup-pushes",
        type=int,
        default=DEFAULT_WARMUP_PUSHES,
        help="Pushes at the start of the history excluded from every window, so a cold-start batch never counts. Default: %(default)s.",
    )
    parser.add_argument(
        "--latency",
        choices=["detect", "detect_pushes", "window_pushes", "detect_ci", "identify"],
        default="detect",
        help="Latency to optimize: hours until the first run after the culprit (default), which a schedule controls; pushes from the culprit to the detecting run, which quiet hours do not inflate; pushes from the previous run to the detecting one, what a sheriff bisects; or, with --capacity, hours until the catching run completes (detect_ci) or until the backfills pin the culprit (identify). All are always reported. The feasibility constraint follows it: a strategy must have the latency for every regression production has it for, since later events can be missing where earlier ones exist.",
    )
    parser.add_argument(
        "--bootstrap",
        type=int,
        default=0,
        metavar="SAMPLES",
        help="Bootstrap samples for a 95%% interval of the mean latencies, 0 to skip. Default: %(default)s.",
    )
    parser.add_argument(
        "--baseline",
        choices=sorted(BASELINES),
        default="recorded",
        help="Production baseline: the full-suite pushes that actually ran (default), or the backstop rule replayed over the pushes.",
    )
    parser.add_argument(
        "--risk-scores",
        metavar="PATH",
        help="JSON lines file shaped like perf_commit_risk.json to use instead of the published dataset.",
    )
    parser.add_argument(
        "--model-version",
        help="Risk score model version to use when the records hold several.",
    )
    parser.add_argument(
        "--calibration",
        metavar="MODE",
        help="How the risk strategies see the scores. fit (default): the pipeline's isotonic mapping to culprit rates, fitted on the labeled commits of the optimize window (optimize) or of the history before the window (simulate) and applied to every commit, as the endpoint serves a table fitted on the past. published: the pipeline's perf_risk_calibration.json for the model version. none: the model's raw output. Or a PATH to a JSON lines file of calibration records.",
    )
    parser.add_argument(
        "--output", metavar="PATH", help="Write the full results as JSON."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="op", required=True)

    simulate = subparsers.add_parser(
        "simulate", help="Replay strategies over a window and compare them."
    )
    simulate.add_argument(
        "--strategy",
        action="append",
        metavar="SPEC",
        help="production, preset:<shadow scheduler name>, or family:key=value,... (e.g. fixed_interval:push_interval=64, rapb:threshold=0.77,aging_rate=0.018). Repeatable. Default: production and every preset.",
    )
    _add_common(simulate)

    optimize = subparsers.add_parser(
        "optimize",
        help="Tune each family on an older window and evaluate the Pareto front on a newer one.",
    )
    optimize.add_argument(
        "--family",
        action="append",
        choices=TUNABLE_FAMILIES,
        help="Family to tune. Repeatable. Default: all.",
    )
    optimize.add_argument(
        "--optimize-until",
        type=parse_timestamp,
        required=True,
        metavar="DATE",
        help="Boundary between the optimize window (before) and the eval window (after).",
    )
    optimize.add_argument(
        "--trials",
        type=int,
        default=DEFAULT_TRIALS,
        help="Trials per family: the values swept for a one-parameter family (every integer for push intervals) and the NSGA-II trials for a two-parameter one. Default: %(default)s.",
    )
    optimize.add_argument(
        "--statistic",
        choices=["mean", "median", "p90", "max"],
        default="mean",
        help="Summary of the latency over the window's regressions to minimize. The maximum rests on a single regression per window, so it is the noisiest. Default: %(default)s.",
    )
    optimize.add_argument("--seed", type=int, default=0, help="Sampler seed.")
    optimize.add_argument(
        "--cost",
        choices=["jobs", "batches", "total_jobs"],
        default="jobs",
        help="Cost objective: jobs counts every task run, a batch as the size of the suite at the time and production's expanded runs as well; batches counts full-suite runs only; total_jobs, with --capacity, adds the sheriff's backfills. Default: %(default)s.",
    )
    optimize.add_argument(
        "--max-cost-ratio",
        type=float,
        default=None,
        help="With --capacity: prune trials whose plain job count exceeds this multiple of production's before the CI replay, which dense schedules make slow. Default: no limit.",
    )
    optimize.add_argument(
        "--markdown", metavar="PATH", help="Write the summary table as markdown."
    )
    _add_common(optimize)

    fetch = subparsers.add_parser(
        "fetch-capacity",
        help="Build the CI capacity model from the fxci dataset: pools, label run times, background load.",
    )
    fetch.add_argument("--since", type=parse_timestamp, required=True, metavar="DATE")
    fetch.add_argument(
        "--until",
        type=parse_timestamp,
        metavar="DATE",
        help="Default: now. Use the span of the pushes you will replay.",
    )
    fetch.add_argument(
        "--min-share",
        type=float,
        default=0.005,
        help="Keep pools carrying at least this share of autoland perf runs. Default: %(default)s.",
    )
    fetch.add_argument(
        "--no-list-workers",
        action="store_true",
        help="Skip the Taskcluster queue's current worker listing.",
    )
    fetch.add_argument("--output", metavar="PATH", required=True)

    validate = subparsers.add_parser(
        "validate-capacity",
        help="Replay production through the capacity model and compare simulated queue waits with the observed ones.",
    )
    validate.add_argument(
        "--fit-scales",
        action="store_true",
        help="Fit each pool's effective share of workers to the observed median wait and save it into the capacity file.",
    )
    _add_common(validate)

    args = parser.parse_args()
    if args.op == "simulate":
        cmd_simulate(args)
    elif args.op == "optimize":
        cmd_optimize(args)
    elif args.op == "fetch-capacity":
        cmd_fetch_capacity(args)
    else:
        if not args.capacity:
            parser.error("validate-capacity needs --capacity")
        cmd_validate_capacity(args)


if __name__ == "__main__":
    main()
