#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from causalperf_reference.artifacts import digest
from causalperf_reference.decision import decide
from causalperf_reference.gates import (
    verify_correctness,
    verify_environment,
    verify_integrity,
    verify_intervention_isolation,
    verify_mechanism,
    verify_replication,
)
from causalperf_reference.statistics import verify_a1_b_a2


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    payload = json.loads((ROOT / "reference/examples/synthetic-pass.json").read_text())
    manifests = payload["integrity_inputs"]["source_manifests"]
    for manifest in manifests:
        manifest["tree_sha256"] = digest(manifest["entries"])
    baseline_tree = next(item["tree_sha256"] for item in manifests if item["role"] == "BASELINE")
    intervention = payload["intervention"] | {
        "rollback": {"baseline_source_sha256": baseline_tree}
    }
    stats = verify_a1_b_a2(
        payload["a1"], payload["treatment"], payload["a2"],
        absolute_threshold_ms=50,
        relative_threshold_percent=10,
        max_baseline_drift_percent=5,
        bootstrap_resamples=10_000,
        seed=42,
    )
    integrity = verify_integrity(intervention, payload["integrity_inputs"])
    correctness = verify_correctness(payload["correctness_reports"])
    environment = verify_environment(payload["environments"], payload["environment_policy"])
    mechanism = verify_mechanism(payload["prediction"], payload["evidence_by_arm"])
    replication = verify_replication(stats.absolute_effect_ms, payload["replication_effects_ms"])
    isolation = verify_intervention_isolation(intervention)
    decision = decide(
        prediction_registered_at=payload["prediction_registered_at"],
        first_treatment_at=payload["first_treatment_at"],
        integrity=integrity.status, correctness=correctness.status,
        environment=environment.status, mechanism=mechanism.status,
        statistics=stats.status, replication=replication.status,
        isolation=isolation.status,
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
            "integrity": integrity.to_dict(),
            "correctness": correctness.to_dict(),
            "environment": environment.to_dict(),
            "isolation": isolation.to_dict(),
        },
        "statistics": {"status": stats.status, "reason_codes": list(stats.reason_codes)},
        "mechanism": mechanism.to_dict(),
        "replication": replication.to_dict(),
        "decision": decision.verdict,
    }
    schema = json.loads((ROOT / "schemas/experiment.schema.json").read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(record)
    print(json.dumps({"gates": record["gates"] | {
        "statistics": record["statistics"], "mechanism": record["mechanism"],
        "replication": record["replication"]}, "decision": decision.to_dict()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
