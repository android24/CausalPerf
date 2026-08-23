from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, asdict
from typing import Any


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
    excluded: dict[str, int]
    descriptive: dict[str, dict[str, float]]
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


def _describe(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    center = statistics.median(ordered)
    p90_index = max(0, math.ceil(0.9 * len(ordered)) - 1)
    return {
        "median": center,
        "p90": ordered[p90_index],
        "mad": statistics.median(abs(value - center) for value in ordered),
        "minimum": ordered[0],
        "maximum": ordered[-1],
    }


def _bootstrap_median_effect(
    baseline: list[float], treatment: list[float], *, resamples: int, seed: int,
    expected_direction: str = "decrease", confidence_level: float = 0.95,
) -> tuple[float, float]:
    rng = random.Random(seed)
    effects = []
    for _ in range(resamples):
        a = [rng.choice(baseline) for _ in baseline]
        b = [rng.choice(treatment) for _ in treatment]
        raw = statistics.median(a) - statistics.median(b)
        effects.append(raw if expected_direction == "decrease" else -raw)
    effects.sort()
    alpha = 1 - confidence_level
    lower = effects[int(alpha / 2 * (resamples - 1))]
    upper = effects[int((1 - alpha / 2) * (resamples - 1))]
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
    confidence_level: float = 0.95,
    minimum_included_per_arm: int = 10,
    expected_direction: str = "decrease",
    threshold_combination: str = "both",
    excluded: dict[str, int] | None = None,
    invalid_sample_limit_exceeded: bool = False,
) -> StatisticalVerdict:
    a1 = _finite(a1, "a1")
    treatment = _finite(treatment, "treatment")
    a2 = _finite(a2, "a2")
    if min(len(a1), len(treatment), len(a2)) < minimum_included_per_arm:
        reasons = ("INSUFFICIENT_SAMPLES",)
    else:
        reasons = ()

    a1_median = statistics.median(a1)
    a2_median = statistics.median(a2)
    drift = abs(_relative_change(a1_median, a2_median))
    baseline = a1 + a2
    baseline_median = statistics.median(baseline)
    treatment_median = statistics.median(treatment)
    raw_effect = baseline_median - treatment_median
    absolute_effect = raw_effect if expected_direction == "decrease" else -raw_effect
    relative_effect = absolute_effect / max(baseline_median, 1e-12) * 100
    ci = _bootstrap_median_effect(
        baseline, treatment, resamples=bootstrap_resamples, seed=seed,
        expected_direction=expected_direction, confidence_level=confidence_level,
    )

    reason_list = list(reasons)
    if drift > max_baseline_drift_percent:
        reason_list.append("BASELINE_DRIFT")
    absolute_pass = absolute_effect >= absolute_threshold_ms
    relative_pass = relative_effect >= relative_threshold_percent
    practical_pass = (absolute_pass or relative_pass) if threshold_combination == "either" else (absolute_pass and relative_pass)
    if not practical_pass:
        if not absolute_pass:
            reason_list.append("ABSOLUTE_EFFECT_TOO_SMALL")
        if not relative_pass:
            reason_list.append("RELATIVE_EFFECT_TOO_SMALL")
    if ci[0] <= 0:
        reason_list.append("EFFECT_UNCERTAIN")
    if invalid_sample_limit_exceeded:
        reason_list.append("INVALID_SAMPLE_LIMIT_EXCEEDED")

    inconclusive_reasons = {"BASELINE_DRIFT", "INSUFFICIENT_SAMPLES", "EFFECT_UNCERTAIN", "INVALID_SAMPLE_LIMIT_EXCEEDED"}
    if any(reason in inconclusive_reasons for reason in reason_list):
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
        excluded=excluded or {"a1": 0, "treatment": 0, "a2": 0},
        descriptive={"a1": _describe(a1), "treatment": _describe(treatment), "a2": _describe(a2)},
        reason_codes=tuple(reason_list),
    )


def _sets_for_metric(measurement_sets: list[dict], metric: str) -> dict[str, dict]:
    return {item["arm"]: item for item in measurement_sets if item["metric"] == metric}


def _arm_values(item: dict) -> tuple[list[float], int, bool]:
    included = [float(value["value"]) for value in item["measurements"] if value["included"]]
    excluded = len(item["measurements"]) - len(included)
    invalid_percent = 100 * excluded / len(item["measurements"])
    return included, excluded, invalid_percent > item["policy"]["max_invalid_percent"]


def _bootstrap_regression_percent(
    baseline: list[float], treatment: list[float], *, direction: str,
    resamples: int, seed: int, confidence_level: float,
) -> tuple[float, float]:
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(resamples):
        a = [rng.choice(baseline) for _ in baseline]
        b = [rng.choice(treatment) for _ in treatment]
        base = statistics.median(a)
        raw = (statistics.median(b) - base) / max(abs(base), 1e-12) * 100
        values.append(raw if direction == "increase" else -raw)
    values.sort()
    alpha = 1 - confidence_level
    return (
        values[int(alpha / 2 * (resamples - 1))],
        values[int((1 - alpha / 2) * (resamples - 1))],
    )


def verify_experiment_statistics(measurement_sets: list[dict], prediction: dict, policy: dict) -> dict[str, Any]:
    """Compute the preregistered A1/B/A2 primary and protected-metric family."""
    if policy["design"] != "a1_b_a2":
        raise ValueError(f"unsupported preregistered design: {policy['design']}")
    primary_sets = _sets_for_metric(measurement_sets, prediction["primary_metric"])
    missing_primary = [arm for arm in ("A1", "B", "A2") if arm not in primary_sets]
    if missing_primary:
        return {"status": "INCONCLUSIVE", "reason_codes": [f"PRIMARY_ARM_MISSING:{arm}" for arm in missing_primary], "primary": None, "protected_secondary": {}, "multiplicity": {"method": "bonferroni", "family_size": 0}}

    values: dict[str, list[float]] = {}
    excluded: dict[str, int] = {}
    invalid = False
    for arm, label in (("A1", "a1"), ("B", "treatment"), ("A2", "a2")):
        values[label], excluded[label], exceeded = _arm_values(primary_sets[arm])
        invalid = invalid or exceeded
    if any(not values[label] for label in ("a1", "treatment", "a2")):
        return {"status": "INCONCLUSIVE", "reason_codes": ["PRIMARY_INCLUDED_SAMPLES_MISSING"], "primary": None, "protected_secondary": {}, "multiplicity": {"method": policy["multiplicity"]["method"], "family_size": len(prediction.get("protected_secondary_metrics", []))}}
    effect = prediction["minimum_effect"]
    primary = verify_a1_b_a2(
        values["a1"], values["treatment"], values["a2"],
        absolute_threshold_ms=effect["absolute"],
        relative_threshold_percent=effect["relative_percent"],
        max_baseline_drift_percent=policy["max_baseline_drift_percent"],
        bootstrap_resamples=policy["bootstrap_resamples"], seed=policy["seed"],
        confidence_level=policy["confidence_level"],
        minimum_included_per_arm=policy["minimum_included_per_arm"],
        expected_direction=prediction["expected_direction"],
        threshold_combination=effect["combination"], excluded=excluded,
        invalid_sample_limit_exceeded=invalid,
    )

    protected = prediction.get("protected_secondary_metrics", [])
    family_size = len(protected)
    simultaneous_confidence = 1 - (1 - policy["confidence_level"]) / max(family_size, 1)
    secondary_results: dict[str, dict] = {}
    for index, specification in enumerate(protected):
        metric = specification["metric"]
        sets = _sets_for_metric(measurement_sets, metric)
        missing = [arm for arm in ("A1", "B", "A2") if arm not in sets]
        if missing:
            secondary_results[metric] = {"status": "INCONCLUSIVE", "reason_codes": [f"PROTECTED_ARM_MISSING:{arm}" for arm in missing]}
            continue
        a1, excluded_a1, invalid_a1 = _arm_values(sets["A1"])
        treatment, excluded_b, invalid_b = _arm_values(sets["B"])
        a2, excluded_a2, invalid_a2 = _arm_values(sets["A2"])
        if not a1 or not treatment or not a2 or min(len(a1), len(treatment), len(a2)) < policy["minimum_included_per_arm"]:
            secondary_results[metric] = {"status": "INCONCLUSIVE", "reason_codes": ["INSUFFICIENT_PROTECTED_SAMPLES"]}
            continue
        baseline = a1 + a2
        baseline_median = statistics.median(baseline)
        treatment_median = statistics.median(treatment)
        raw_regression = (treatment_median - baseline_median) / max(abs(baseline_median), 1e-12) * 100
        regression = raw_regression if specification["regression_direction"] == "increase" else -raw_regression
        interval = _bootstrap_regression_percent(
            baseline, treatment, direction=specification["regression_direction"],
            resamples=policy["bootstrap_resamples"], seed=policy["seed"] + index + 1,
            confidence_level=simultaneous_confidence,
        )
        margin = specification["maximum_regression_percent"]
        reasons: list[str] = []
        if invalid_a1 or invalid_b or invalid_a2:
            status = "INCONCLUSIVE"; reasons.append("INVALID_SAMPLE_LIMIT_EXCEEDED")
        elif interval[0] > margin:
            status = "FAIL"; reasons.append("PROTECTED_METRIC_REGRESSED")
        elif interval[1] > margin:
            status = "INCONCLUSIVE"; reasons.append("PROTECTED_METRIC_NONINFERIORITY_UNCERTAIN")
        else:
            status = "PASS"
        secondary_results[metric] = {
            "status": status, "reason_codes": reasons,
            "baseline_median": baseline_median, "treatment_median": treatment_median,
            "regression_percent": regression, "confidence_interval_percent": interval,
            "maximum_regression_percent": margin, "simultaneous_confidence_level": simultaneous_confidence,
            "included": {"a1": len(a1), "treatment": len(treatment), "a2": len(a2)},
            "excluded": {"a1": excluded_a1, "treatment": excluded_b, "a2": excluded_a2},
        }

    secondary_statuses = {item["status"] for item in secondary_results.values()}
    if "FAIL" in secondary_statuses:
        status = "FAIL"; reasons = ["PROTECTED_SECONDARY_METRIC_FAILED"]
    elif primary.status == "INCONCLUSIVE" or "INCONCLUSIVE" in secondary_statuses:
        status = "INCONCLUSIVE"; reasons = ["STATISTICAL_EVIDENCE_INCONCLUSIVE"]
    elif primary.status == "FAIL":
        status = "FAIL"; reasons = ["PRIMARY_METRIC_FAILED"]
    else:
        status = "PASS"; reasons = []
    return {
        "status": status, "reason_codes": reasons, "primary": primary.to_dict(),
        "protected_secondary": secondary_results,
        "multiplicity": {"method": policy["multiplicity"]["method"], "family_size": family_size, "simultaneous_confidence_level": simultaneous_confidence},
    }
