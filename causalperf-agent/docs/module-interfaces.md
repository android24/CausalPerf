# Module interfaces

Modules communicate through versioned artifacts, not shared mutable dictionaries.

## TaskLoader

```text
input:  public task package
output: ValidatedTask | TaskValidationFailure
```

Validates schema, content digests, path containment, budgets, and command shape.
It does not execute commands or read private evaluator data.

## EnvironmentController

```text
input:  ValidatedTask + requested device
output: EnvironmentSnapshot + EnvironmentGateResult
```

Resolves exactly one device, verifies platform constraints, records compilation
and environmental state, and provides explicit normalization actions.

## ReproductionRunner

```text
input:  ValidatedTask + EnvironmentSnapshot
output: ReproductionResult + baseline MeasurementSet + artifacts
```

Establishes whether the problem is reproducible within budget. Failure produces
`INCONCLUSIVE`, not permission to mutate source blindly.

## EvidenceEngine

```text
input:  immutable traces/logs/measurements + collector policy
output: EvidenceBundle
```

Evidence contains provenance, timestamps, units, query versions, and validity.
The engine does not assign causal status.

## HypothesisEngine

```text
input:  PerformanceTask + EvidenceBundle + permitted source context
output: ordered HypothesisSet
```

Each hypothesis contains mechanism, supporting/contradicting evidence,
alternative explanations, candidate falsifiers, and uncertainty.

## ExperimentPlanner

```text
input:  one hypothesis + task policy + source context
output: PredictionRegistration + InterventionPlan
```

The registration is sealed in the ledger before any treatment result is
available. The plan must specify one primary factor, expected mechanism change,
minimum meaningful effect, protected metrics, risks, and rollback.

## InterventionRunner

```text
input:  approved InterventionPlan
output: PatchArtifact + BuildArtifact | InterventionFailure
```

Checks permitted paths before and after mutation. It cannot edit task,
benchmark, correctness, evaluator, or Ground Truth content.

## GateRunner

```text
input:  task + baseline/treatment artifacts
output: IntegrityGateResult + CorrectnessGateResult + EnvironmentGateResult
```

Any mandatory FAIL prevents an accepted optimization and triggers rollback.

## BenchmarkRunner

```text
input:  built artifact + sequence plan + environment policy
output: MeasurementSet + runtime artifacts
```

It records all samples and preregistered exclusions. It cannot choose a new
sequence after observing unfavorable results.

## StatisticalVerifier

```text
input:  baseline/treatment MeasurementSets + registered prediction
output: StatisticalVerdict + MechanismVerdict
```

Reports practical effect, uncertainty, drift, and protected metrics. It never
overrides correctness, integrity, or environment failures.

## DecisionEngine

```text
input:  all gate results + hypothesis state + experiment budget
output: ACCEPT | REJECT | INCONCLUSIVE | ROLLBACK_REQUIRED
```

The decision table is deterministic and versioned. An LLM may explain a
decision but cannot change it.

## ExperimentLedger

```text
append(event) -> event digest
verify()      -> LedgerVerificationResult
```

Events are ordered, hash-chained, immutable, and free of secrets and private
Ground Truth.

