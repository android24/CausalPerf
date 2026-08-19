from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class StatisticalVerdict:
    status: str
    baseline_median_ms: float
    treatment_median_ms: float
    absolute_effect_ms: float
    relative_effect_percent: float
    confidence_interval_ms: tuple[float, float]
    baseline_drift_percent: float
    included: dict[str, int]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _finite(values: list[float], arm: str) -> list[float]:
    if not values:
        raise ValueError(f"{arm} has no measurements")
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError(f"{arm} contains invalid measurements")
    return values


def _relative_change(old: float, new: float) -> float:
    return 0.0 if old == new == 0 else (new - old) / max(abs(old), 1e-12) * 100


def _bootstrap_median_effect(
    baseline: list[float], treatment: list[float], *, resamples: int, seed: int
) -> tuple[float, float]:
    rng = random.Random(seed)
    effects = []
    for _ in range(resamples):
        a = [rng.choice(baseline) for _ in baseline]
        b = [rng.choice(treatment) for _ in treatment]
        effects.append(statistics.median(a) - statistics.median(b))
    effects.sort()
    lower = effects[int(0.025 * (resamples - 1))]
    upper = effects[int(0.975 * (resamples - 1))]
    return lower, upper


def verify_a1_b_a2(
    a1: list[float],
    treatment: list[float],
    a2: list[float],
    *,
    absolute_threshold_ms: float,
    relative_threshold_percent: float,
    max_baseline_drift_percent: float,
    bootstrap_resamples: int = 10_000,
    seed: int = 0,
) -> StatisticalVerdict:
    a1 = _finite(a1, "a1")
    treatment = _finite(treatment, "treatment")
    a2 = _finite(a2, "a2")
    if min(len(a1), len(treatment), len(a2)) < 10:
        reasons = ("INSUFFICIENT_SAMPLES",)
    else:
        reasons = ()

    a1_median = statistics.median(a1)
    a2_median = statistics.median(a2)
    drift = abs(_relative_change(a1_median, a2_median))
    baseline = a1 + a2
    baseline_median = statistics.median(baseline)
    treatment_median = statistics.median(treatment)
    absolute_effect = baseline_median - treatment_median
    relative_effect = absolute_effect / max(baseline_median, 1e-12) * 100
    ci = _bootstrap_median_effect(
        baseline, treatment, resamples=bootstrap_resamples, seed=seed
    )

    reason_list = list(reasons)
    if drift > max_baseline_drift_percent:
        reason_list.append("BASELINE_DRIFT")
    if absolute_effect < absolute_threshold_ms:
        reason_list.append("ABSOLUTE_EFFECT_TOO_SMALL")
    if relative_effect < relative_threshold_percent:
        reason_list.append("RELATIVE_EFFECT_TOO_SMALL")
    if ci[0] <= 0:
        reason_list.append("EFFECT_UNCERTAIN")

    if "BASELINE_DRIFT" in reason_list or "INSUFFICIENT_SAMPLES" in reason_list:
        status = "INCONCLUSIVE"
    elif reason_list:
        status = "FAIL"
    else:
        status = "PASS"

    return StatisticalVerdict(
        status=status,
        baseline_median_ms=baseline_median,
        treatment_median_ms=treatment_median,
        absolute_effect_ms=absolute_effect,
        relative_effect_percent=relative_effect,
        confidence_interval_ms=ci,
        baseline_drift_percent=drift,
        included={"a1": len(a1), "treatment": len(treatment), "a2": len(a2)},
        reason_codes=tuple(reason_list),
    )

