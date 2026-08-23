from __future__ import annotations

from datetime import datetime, timezone

from .artifacts import digest, verify_experiment_bundle
from .decision import decide
from .gates import (
    verify_correctness,
    verify_environment,
    verify_integrity,
    verify_intervention_isolation,
    verify_mechanism,
    verify_replication,
)
from .ledger import Ledger
from .statistics import verify_experiment_statistics


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def evaluate_bundle(bundle: dict) -> dict:
    """Pure reference evaluator; executes no shell or device mutation."""
    verify_experiment_bundle(bundle)
    ledger = Ledger(bundle["run_id"])

    def record(phase: str, kind: str, inputs: list[str] | None = None, outputs: list[str] | None = None):
        ledger.append({"occurred_at": _now(), "actor": "RUNNER", "phase": phase, "kind": kind,
                       "inputs": inputs or [], "outputs": outputs or [], "exit_status": 0})

    record("VALIDATE", "COMPLETION")
    primary_metric = bundle["prediction"]["primary_metric"]
    arms = {item["arm"]: item for item in bundle["measurement_sets"] if item["metric"] == primary_metric}
    record("MEASURE_A1", "COMPLETION", outputs=[arms["A1"]["content_sha256"]])
    record("REGISTER", "COMPLETION", outputs=[bundle["prediction"]["content_sha256"]])
    record("MEASURE_B", "COMPLETION", outputs=[arms["B"]["content_sha256"]])
    record("MEASURE_A2", "COMPLETION", outputs=[arms["A2"]["content_sha256"]])

    policy = bundle["statistical_policy"]
    statistical = verify_experiment_statistics(bundle["measurement_sets"], bundle["prediction"], policy)
    environment = verify_environment(bundle["environments"], bundle["environment_policy"])
    integrity = verify_integrity(bundle["intervention"], bundle["integrity_inputs"])
    correctness = verify_correctness(bundle["correctness_reports"])
    isolation = verify_intervention_isolation(bundle["intervention"])
    mechanism = verify_mechanism(bundle["prediction"], bundle["evidence_by_arm"])
    primary_effect = statistical["primary"]["absolute_effect_ms"] if statistical["primary"] else 0
    replication = verify_replication(primary_effect, bundle.get("replication_effects_ms", []),
                                     tolerance_percent=policy.get("replication_tolerance_percent", 20))
    first_b = min(item["measured_at"] for item in arms["B"]["measurements"])
    decision = decide(prediction_registered_at=bundle["prediction"]["registered_at"], first_treatment_at=first_b,
                      integrity=integrity.status, correctness=correctness.status,
                      environment=environment.status, mechanism=mechanism.status,
                      statistics=statistical["status"], replication=replication.status,
                      isolation=isolation.status)
    computed_gates = {
        "integrity": integrity.to_dict(), "correctness": correctness.to_dict(),
        "isolation": isolation.to_dict(), "environment": environment.to_dict(),
        "mechanism": mechanism.to_dict(), "replication": replication.to_dict(),
    }
    record(
        "VERIFY", "COMPLETION",
        inputs=[
            bundle["integrity_inputs"]["content_sha256"],
            *(item["content_sha256"] for item in bundle["correctness_reports"]),
            *(item["content_sha256"] for item in bundle["environments"]),
            bundle["environment_policy"]["content_sha256"],
            bundle["statistical_policy"]["content_sha256"],
            bundle["prediction"]["content_sha256"],
            *(item["content_sha256"] for items in bundle["evidence_by_arm"].values() for item in items),
        ],
        outputs=[digest(computed_gates), digest(statistical)],
    )
    result = {
        "run_id": bundle["run_id"], "statistics": statistical,
        "gates": computed_gates, "decision": decision.to_dict()
    }
    record("DECIDE", "DECISION", outputs=[digest(result)])
    result["ledger"] = ledger.events
    result["ledger_head_sha256"] = ledger.verify()
    return result
