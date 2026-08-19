# Experiment execution protocol

This protocol defines required orchestration behavior. A pure, non-mutating
reference evaluator now exercises bundle validation, A1/B/A2 statistics,
computed environment/mechanism/replication gates, causal decision, and a
hash-chained ledger. It is not the Android command/device runner: build, adb,
Perfetto, recovery, and rollback side effects remain unimplemented.

| Step | Preconditions | Outputs | Failure transition |
|---|---|---|---|
| VALIDATE | public task received | validated task, content manifest | INVALID |
| PREPARE | one approved device and toolchain | EnvironmentSnapshot | INCONCLUSIVE |
| BUILD_A | clean source revision | baseline APK digest | FAILED |
| CORRECTNESS_A | baseline installed | baseline correctness gate | INVALID_TASK |
| MEASURE_A1 | environment valid | A1 MeasurementSet and traces | INCONCLUSIVE |
| DIAGNOSE | A1 sealed | EvidenceBundle and hypotheses | INCONCLUSIVE |
| REGISTER | experiment plan approved | immutable prediction digest | INVALID if late |
| APPLY_B | writable scope and approval valid | patch digest | ROLLBACK_REQUIRED |
| BUILD_B | patch applied | treatment APK digest | ROLLBACK_REQUIRED |
| CORRECTNESS_B | treatment installed | correctness/integrity gates | REJECT + rollback |
| MEASURE_B | environment valid | B MeasurementSet and traces | INCONCLUSIVE + rollback |
| RESTORE_A | baseline source digest known | restored source/APK digest | ROLLBACK_REQUIRED |
| MEASURE_A2 | restored baseline and valid environment | A2 MeasurementSet | INCONCLUSIVE |
| VERIFY | A1/B/A2 and mechanism evidence sealed | statistical/mechanism verdicts | INCONCLUSIVE |
| DECIDE | all mandatory gates available | deterministic decision | none |
| CLEANUP | decision recorded | cleanup and ledger verification | ROLLBACK_REQUIRED |

## Command envelope

Every command is structured:

```text
executable, argument vector, working directory
environment allowlist, device serial, package scope
timeout, output byte limit, owned-process identifier
idempotency declaration, expected artifacts, retry policy
```

Model-generated shell strings are not an execution interface.

## Retry policy

- Schema, integrity, correctness, and deterministic build failures are not
  retried automatically.
- Device transport failures may be retried once only when no mutation is in
  flight and the task budget permits it.
- Performance samples are never selectively retried based on their metric.
- If invalid samples exceed policy, return `INCONCLUSIVE` rather than extending
  the experiment until it passes.

## Artifact association

Every measurement iteration has a run ID, arm, block, sequence position,
environment snapshot, APK digest, source digest, benchmark result, and trace
digest. A trace without this association cannot be used as causal evidence.

## Recovery boundary

Write an intent ledger event before each side effect and a completion event
after it. On restart, compare workspace/device digests with the last completion
event. Resume only from a declared safe boundary; otherwise roll back.
