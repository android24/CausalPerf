#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from causalperf_reference.decision import decide
from causalperf_reference.statistics import verify_a1_b_a2


ROOT = Path(__file__).resolve().parents[1]


def gate(status: str, *reasons: str) -> dict:
    return {"status": status, "reason_codes": list(reasons)}


def main() -> int:
    payload = json.loads((ROOT / "reference/examples/synthetic-pass.json").read_text())
    stats = verify_a1_b_a2(
        payload["a1"], payload["treatment"], payload["a2"],
        absolute_threshold_ms=50,
        relative_threshold_percent=10,
        max_baseline_drift_percent=5,
        bootstrap_resamples=10_000,
        seed=42,
    )
    decision = decide(
        prediction_registered_at=payload["prediction_registered_at"],
        first_treatment_at=payload["first_treatment_at"],
        integrity="PASS", correctness="PASS", environment="PASS",
        mechanism="PASS", statistics=stats.status, replication="PASS",
    )
    record = {
        "schema_version": 1,
        "run_id": payload["run_id"],
        "task_id": payload["task_id"],
        "prediction_id": payload["prediction_id"],
        "prediction_registered_at": payload["prediction_registered_at"],
        "first_treatment_at": payload["first_treatment_at"],
        "design": "a1_b_a2",
        "arms": {
            "a1": payload["a1"],
            "treatment": payload["treatment"],
            "a2": payload["a2"],
        },
        "gates": {
            "integrity": gate("PASS"),
            "correctness": gate("PASS"),
            "environment": gate("PASS"),
        },
        "statistics": gate(stats.status, *stats.reason_codes),
        "mechanism": gate("PASS"),
        "replication": gate("PASS"),
        "decision": decision.verdict,
    }
    schema = json.loads((ROOT / "schemas/experiment.schema.json").read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(record)
    print(json.dumps({"statistics": stats.to_dict(), "decision": decision.to_dict()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

