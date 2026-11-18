# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Acceptance is computed from CI observations, never from the model's verdict."""

from __future__ import annotations

import math
from statistics import mean, variance

from scipy.stats import t

from .evidence import metric_key

# Perfherder returns values rounded to two decimals. Rounding alone adds this
# much variance (uniform error), so identical rounded values never mean zero
# uncertainty.
RESOLUTION = 0.01
ROUNDING_VARIANCE = RESOLUTION**2 / 12


def values(points: list[dict]) -> list[float]:
    seen = set()
    result = []
    for point in points:
        job = point.get("job_id")
        value = point.get("value")
        if type(job) is not int or job in seen:
            raise ValueError("Missing or duplicate independent CI job identity")
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ValueError("Measurement must be finite and nonnegative")
        seen.add(job)
        result.append(float(value))
    return result


def difference_interval(
    a: list[float], b: list[float], alpha: float
) -> tuple[float, float]:
    """Welch confidence bounds for mean(a) - mean(b)."""
    va = max(variance(a), ROUNDING_VARIANCE) / len(a)
    vb = max(variance(b), ROUNDING_VARIANCE) / len(b)
    delta = mean(a) - mean(b)
    degrees = (va + vb) ** 2 / (va**2 / (len(a) - 1) + vb**2 / (len(b) - 1))
    margin = float(t.ppf(1 - alpha / 2, degrees)) * math.sqrt(va + vb)
    return delta - margin, delta + margin


def evaluate(
    rows: list[dict],
    good: dict,
    bad: dict,
    candidate: dict,
    *,
    minimum: int = 2,
    recovery: float = 0.8,
) -> dict:
    """Report observed improvements and decide whether each metric recovered.

    A metric is "fixed" when the candidate's mean recovers at least ``recovery``
    of the gap between the historical bad and good means. Any positive mean
    change is reported as an improvement. Welch intervals (95% family-wise,
    with a variance floor for Perfherder's 0.01 rounding) are reported to help
    judge noise but do not decide the verdict. A missing platform, incomplete
    data, or baselines that do not show the regression are inconclusive.
    """
    if not rows or minimum < 2 or not 0 < recovery <= 1:
        raise ValueError("Invalid validation policy")
    results = []
    alpha = 0.05 / (len(rows) * 3)
    for row in rows:
        sig = row["series_signature"]
        key = metric_key(sig)
        label = f"{sig['machine_platform']} {sig['suite']} {sig.get('test', '')}"
        if sig.get("extra_options"):
            label += f" ({', '.join(sorted(sig['extra_options']))})"
        try:
            g, b, c = [values(group.get(key, [])) for group in (good, bad, candidate)]
        except ValueError as exc:
            results.append(
                {
                    "metric": label,
                    "key": key,
                    "status": "inconclusive",
                    "reason": str(exc),
                }
            )
            continue
        # Positive always means worse, for both score and duration metrics.
        sign = 1 if sig.get("lower_is_better", True) else -1
        gm, bm, cm = [mean(group) if group else None for group in (g, b, c)]
        improved = bm is not None and cm is not None and sign * (bm - cm) > 0
        result = {
            "metric": label,
            "key": key,
            "unit": sig.get("measurement_unit") or "",
            "good_mean": gm,
            "bad_mean": bm,
            "candidate_mean": cm,
            "counts": [len(g), len(b), len(c)],
            "lower_is_better": sign == 1,
            "improved": improved,
            "improvement_pct": 100 * sign * (bm - cm) / bm
            if bm and cm is not None
            else None,
            "residual_pct": 100 * sign * (cm - gm) / gm
            if gm and cm is not None
            else None,
        }
        # Preserve measured improvements even when history cannot support bounds.
        missing = [
            f"{name}: Need at least {required} independent CI jobs, got {len(group)}"
            for name, group, required in (
                ("Historical good", g, 2),
                ("Historical bad", b, 2),
                ("Candidate", c, minimum),
            )
            if len(group) < required
        ]
        if missing:
            result.update(status="inconclusive", reason="; ".join(missing))
            results.append(result)
            continue
        ng, nb, nc = [[sign * x for x in group] for group in (g, b, c)]
        control = difference_interval(nb, ng, alpha)
        improvement = difference_interval(nb, nc, alpha)
        residual = difference_interval(nc, ng, alpha)
        regression = sign * (bm - gm)
        fraction = sign * (bm - cm) / regression if regression > 0 else None
        if fraction is None:
            status = "inconclusive"
            reason = (
                "The historical baselines do not show the regression, so "
                "recovery cannot be judged"
            )
        elif fraction >= recovery:
            status = "fixed"
            reason = f"Candidate recovered {fraction:.0%} of the regression"
        elif improved:
            status = "improved"
            reason = (
                f"Candidate recovered {fraction:.0%} of the regression; "
                f"{recovery:.0%} counts as back to where it was"
            )
        else:
            status = "not_improved"
            reason = "No observed mean improvement"
        result.update(
            {
                "status": status,
                "reason": reason,
                "recovered_fraction": fraction,
                "statistically_significant": improvement[0] > 0,
                "control_regression_ci": control,
                "candidate_improvement_ci": improvement,
                "residual_regression_ci": residual,
            }
        )
        results.append(result)
    return {
        "fixed": all(r["status"] == "fixed" for r in results),
        "positive": any(r.get("improved", False) for r in results),
        "metrics": results,
        "recovery_threshold": recovery,
        "baseline_source": "Perfherder history around the alert push; "
        "no baseline pushes are made",
        "confidence_policy": "Informational 95% family-wise Welch bounds, with "
        "a variance floor for 0.01 rounding; the verdict uses means",
    }
