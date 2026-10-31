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
import json
import logging
from datetime import datetime
from typing import Any

from bugbug import db
from bugbug.perf import datasets, push_status
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


def _ensure(path: str) -> None:
    if not db.exists(path) and not db.download(path):
        raise RuntimeError(f"Failed to download {path}")


def load_timeline(
    need_risks: bool,
    risk_scores: str | None,
    model_version: str | None,
    since: datetime | None = None,
) -> tuple[sim.Timeline, dict[str, tuple[float | None, str | None]] | None, str | None]:
    """Build the timeline from the datasets, with risk scores when a strategy needs them.

    ``risk_scores`` names a JSON lines file shaped like ``perf_commit_risk.json``
    to use instead of the published dataset, such as a fixture. With ``since``
    the history starts there: earlier pushes are dropped, so stateful
    strategies cold-start at the first kept push, which the warm-up excludes.
    Returns the timeline, the raw scores by node and their model version.
    """
    _ensure(datasets.PERF_PUSHES_DB)
    _ensure(datasets.PERF_REGRESSIONS_DB)
    risks = None
    version = None
    if need_risks:
        if risk_scores:
            with open(risk_scores, encoding="utf-8") as fh:
                records: Any = [json.loads(line) for line in fh if line.strip()]
        else:
            _ensure(datasets.PERF_COMMIT_RISK_DB)
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
        unscored = timeline.unscored_commits()
        if unscored:
            raise SystemExit(
                f"{len(unscored)} commits of the pushes have no risk score; the "
                "risk strategies need every commit scored. Run the commit risk "
                "scorer over the window first, or narrow --since/--until."
            )
    return timeline, risks, version


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
        _ensure(datasets.PERF_PUSH_STATUS_DB)
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
            _ensure(datasets.PERF_RISK_CALIBRATION_DB)
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


def _hours(summary: dict[str, Any]) -> str:
    if summary["mean"] is None:
        return "-"
    return "{mean:.1f} / {median:.1f} / {p90:.1f} / {max:.1f}".format(**summary)


def format_table(rows: list[tuple[str, dict[str, Any], bool]]) -> str:
    """A markdown table of per-strategy results; hours are mean / median / p90 / max."""
    header = (
        "| strategy | batches | jobs | regressions | detected | surfaced "
        "| detect hours | surface hours | window pushes (mean) | merged | feasible |"
    )
    lines = [header, "|" + "---|" * 11]
    for name, result, feasible in rows:
        lines.append(
            f"| {name} | {result['batches']} | {result['jobs']} | "
            f"{result['regressions']} | {result['detected']} | {result['surfaced']} | "
            f"{_hours(result['detect_hours'])} | {_hours(result['surface_hours'])} | "
            f"{(result['window_pushes']['mean'] or 0):.1f} | {result['merged']} | "
            f"{'yes' if feasible else 'NO'} |"
        )
    return "\n".join(lines)


def cmd_simulate(args: argparse.Namespace) -> None:
    specs = args.strategy or ["production", *(f"preset:{p}" for p in sim.PRESETS)]
    need_risks = any(
        sim.parse_strategy(spec).family in sim.RISK_FAMILIES
        for spec in specs
        if spec != "production"
    )
    timeline, risks, version = load_timeline(
        need_risks, args.risk_scores, args.model_version, args.since
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

    baseline = sim.run(
        production(timeline, args.baseline),
        timeline,
        [window],
        args.bootstrap,
    )[0]
    rows: list[tuple[str, dict[str, Any], bool]] = [("production", baseline, True)]
    for spec in specs:
        if spec == "production":
            continue
        strategy = resolve_strategy(spec, timeline, args.baseline)
        result = sim.run(strategy, timeline, [window], args.bootstrap)[0]
        rows.append((strategy.name, result, sim.is_feasible(result, baseline)))

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
        f"{metrics[f'{latency}_hours'][statistic]:.1f} | "
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
        need_risks, args.risk_scores, args.model_version, args.since
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
    baseline_optimize, baseline_eval = sim.run(
        production(timeline, args.baseline),
        timeline,
        [optimize_window, eval_window],
        args.bootstrap,
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
            "optimize_window": baseline_optimize["window"],
            "eval_window": baseline_eval["window"],
        },
        "baseline": {"optimize": baseline_optimize, "eval": baseline_eval},
        "families": {},
    }
    headline = [
        f"Baseline (production): {args.cost} {baseline_objectives[0]:.0f} and "
        f"{args.statistic} {args.latency} hours {baseline_objectives[1]:.1f} on the "
        f"optimize window; {args.cost} {eval_objectives[0]:.0f} and {args.statistic} "
        f"{args.latency} hours {eval_objectives[1]:.1f} on the eval window, "
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
        f"| family | question | strategy | {args.cost} | {args.statistic} {args.latency} hours | detected |",
        "|---|---|---|---|---|---|",
    ]
    notes = []
    diagnostics = [
        f"| family | strategy | optimize {args.cost} | optimize {args.statistic} "
        f"{args.latency} h | eval {args.cost} | eval {args.statistic} {args.latency} h |",
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
        if pruned:
            missed = sorted({i for trial in pruned for i in trial["missed_ids"]})
            names = ", ".join(
                sim.FAMILIES[family](**trial["params"]).name for trial in pruned
            )
            notes.append(
                f"{family}: {len(pruned)} trials pruned as too sparse to catch "
                f"regressions {missed} before the pushes end: {names}."
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


def _add_common(parser: argparse.ArgumentParser) -> None:
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
        help="End of the history to evaluate. Default: just after the last regression's culprit push.",
    )
    parser.add_argument(
        "--warmup-pushes",
        type=int,
        default=DEFAULT_WARMUP_PUSHES,
        help="Pushes at the start of the history excluded from every window, so a cold-start batch never counts. Default: %(default)s.",
    )
    parser.add_argument(
        "--latency",
        choices=["detect", "surface"],
        default="detect",
        help="Latency to optimize: until the first run after the culprit (default), which a schedule controls, or until Perfherder would alert, which mostly restates sparsity. Both are always reported.",
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
        choices=["jobs", "batches"],
        default="jobs",
        help="Cost objective: jobs counts every task run, a batch as the size of the suite at the time and production's expanded runs as well; batches counts full-suite runs only. Default: %(default)s.",
    )
    optimize.add_argument(
        "--markdown", metavar="PATH", help="Write the summary table as markdown."
    )
    _add_common(optimize)

    args = parser.parse_args()
    if args.op == "simulate":
        cmd_simulate(args)
    else:
        cmd_optimize(args)


if __name__ == "__main__":
    main()
