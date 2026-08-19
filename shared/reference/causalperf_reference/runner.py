from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from .artifacts import digest, verify_experiment_bundle
from .decision import decide
from .gates import verify_environment, verify_mechanism, verify_replication
from .ledger import Ledger
from .statistics import verify_a1_b_a2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _included(measurement_set: dict) -> list[float]:
    return [item["value"] for item in measurement_set["measurements"] if item["included"]]


def evaluate_bundle(bundle: dict) -> dict:
    """Pure reference evaluator; executes no shell or device mutation."""
    verify_experiment_bundle(bundle)
    ledger = Ledger(bundle["run_id"])

    def record(phase: str, kind: str, inputs: list[str] = [], outputs: list[str] = []):
        ledger.append({"occurred_at": _now(), "actor": "RUNNER", "phase": phase, "kind": kind,
                       "inputs": inputs, "outputs": outputs, "exit_status": 0})

    record("VALIDATE", "COMPLETION")
    arms = {item["arm"]: item for item in bundle["measurement_sets"]}
    record("MEASURE_A1", "COMPLETION", outputs=[arms["A1"]["content_sha256"]])
    record("REGISTER", "COMPLETION", outputs=[bundle["prediction"]["content_sha256"]])
    record("MEASURE_B", "COMPLETION", outputs=[arms["B"]["content_sha256"]])
    record("MEASURE_A2", "COMPLETION", outputs=[arms["A2"]["content_sha256"]])

    policy = bundle["statistical_policy"]
    statistical = verify_a1_b_a2(
        _included(arms["A1"]), _included(arms["B"]), _included(arms["A2"]),
        absolute_threshold_ms=policy["absolute_threshold_ms"],
        relative_threshold_percent=policy["relative_threshold_percent"],
        max_baseline_drift_percent=policy["max_baseline_drift_percent"],
        bootstrap_resamples=policy.get("bootstrap_resamples", 10_000), seed=policy.get("seed", 0))
    environment = verify_environment(bundle["environments"])
    mechanism = verify_mechanism(bundle["prediction"], bundle["evidence_by_arm"])
    replication = verify_replication(statistical.absolute_effect_ms, bundle.get("replication_effects_ms", []),
                                     tolerance_percent=policy.get("replication_tolerance_percent", 20))
    integrity = bundle["integrity_gate"]
    correctness = bundle["correctness_gate"]
    first_b = min(item["measured_at"] for item in arms["B"]["measurements"])
    decision = decide(prediction_registered_at=bundle["prediction"]["registered_at"], first_treatment_at=first_b,
                      integrity=integrity["status"], correctness=correctness["status"],
                      environment=environment.status, mechanism=mechanism.status,
                      statistics=statistical.status, replication=replication.status)
    record("VERIFY", "COMPLETION")
    result = {
        "run_id": bundle["run_id"], "statistics": statistical.to_dict(),
        "gates": {"integrity": integrity, "correctness": correctness,
                  "environment": environment.to_dict(), "mechanism": mechanism.to_dict(),
                  "replication": replication.to_dict()}, "decision": decision.to_dict()
    }
    record("DECIDE", "DECISION", outputs=[digest(result)])
    result["ledger"] = ledger.events
    result["ledger_head_sha256"] = ledger.verify()
    return result
