# Execution state machine

Execution state and hypothesis state are independent.

## Execution states

```text
CREATED
  -> VALIDATING
  -> PREPARING_ENVIRONMENT
  -> BUILDING_BASELINE
  -> VERIFYING_BASELINE_CORRECTNESS
  -> MEASURING_A1
  -> DIAGNOSING
  -> REGISTERING
  -> APPLYING_INTERVENTION
  -> BUILDING_TREATMENT
  -> VERIFYING_TREATMENT_CORRECTNESS
  -> MEASURING_B
  -> RESTORING_BASELINE
  -> MEASURING_A2
  -> VERIFYING
  -> DECIDING
  -> CLEANING_UP
  -> COMPLETED
```

Failure branches may enter `ROLLING_BACK`. Terminal alternatives are
`REJECTED`, `INCONCLUSIVE`, `FAILED`, and `ROLLBACK_REQUIRED`.

## Transition rules

| From | To | Required condition |
|---|---|---|
| CREATED | VALIDATING | run ID and public task received |
| VALIDATING | PREPARING_ENVIRONMENT | schema, digest, scope, and budget valid |
| PREPARING_ENVIRONMENT | BUILDING_BASELINE | environment valid |
| BUILDING_BASELINE | VERIFYING_BASELINE_CORRECTNESS | baseline artifact sealed |
| VERIFYING_BASELINE_CORRECTNESS | MEASURING_A1 | baseline correctness passes |
| MEASURING_A1 | DIAGNOSING | A1 policy satisfied |
| DIAGNOSING | REGISTERING | evidence bundle and competing hypotheses sealed |
| REGISTERING | APPLYING_INTERVENTION | prediction, plan, and approval sealed |
| APPLYING_INTERVENTION | BUILDING_TREATMENT | approved patch applied within scope |
| BUILDING_TREATMENT | VERIFYING_TREATMENT_CORRECTNESS | treatment artifact sealed |
| VERIFYING_TREATMENT_CORRECTNESS | MEASURING_B | correctness and integrity pass |
| MEASURING_B | RESTORING_BASELINE | B policy satisfied |
| RESTORING_BASELINE | MEASURING_A2 | baseline source/device state verified |
| MEASURING_A2 | VERIFYING | A2 policy satisfied |
| VERIFYING | DECIDING | mechanism, statistics and replication gates computed |
| DECIDING | CLEANING_UP | deterministic decision recorded |
| CLEANING_UP | COMPLETED | owned resources cleaned and final state verified |
| any post-mutation failure | ROLLING_BACK | acceptance cannot be established |
| ROLLING_BACK | REJECTED/INCONCLUSIVE/FAILED | baseline restoration verified |
| ROLLING_BACK | ROLLBACK_REQUIRED | restoration cannot be verified |

## Failure semantics

- Validation or reproduction deficiency: `INCONCLUSIVE`, then `COMPLETED`.
- Hypothesis falsified: `REJECTED`; roll back and continue only within the
  experiment budget.
- Correctness/integrity failure: immediate rollback; patch cannot be accepted.
- Build or device failure: preserve artifacts, attempt scoped cleanup, then
  `FAILED` or `ROLLBACK_REQUIRED`.
- User cancellation: stop launching commands, clean owned resources, record
  `CANCELLED`.

## Recovery

Every transition is appended before side effects and completed with a second
event after side effects. On restart, the controller inspects the last completed
transition, verifies workspace and device state, and either resumes at a safe
boundary or enters rollback. Commands are not assumed idempotent unless their
interface explicitly declares it.

`ExecutionSnapshot` is content-addressed and binds the ledger head. Recovery
from JSON reconstructs and verifies the hash chain before inspecting external
state. A missing completion after a mutating intent always rolls back; a
non-idempotent measurement is never repeated automatically. A transport error
may retry once only for a non-mutating idempotent transition.

`FileRunStore` writes an atomic, fsynced checkpoint after intents, completions,
and state changes. Checkpoints bind the snapshot and complete ledger; tampering
or an unsupported version prevents recovery.

When a guarded adapter is installed, policy authorization precedes `INTENT`.
The policy digest, reserved budget and any new rollback obligation are persisted
with the authorization ledger event before dispatch. A denial never creates an
intent or reaches the delegate. An approval-pending request is terminally
`INCONCLUSIVE` for that invocation and may be resubmitted only with an exact,
active approval.

## Implemented reference

`src/causalperf_agent/execution/` contains the typed transition model,
controller, Adapter protocol, Simulated Adapter, persistence and recovery.
Tests inject failures and crashes at every phase and cover PASS, REJECTED,
INCONCLUSIVE, rollback failure, process restart, tamper detection and retry
policy. WP2's execution contract and WP5's authorization integration are Phase
1A complete. Android
Gradle/ADB/Perfetto adapters are intentionally deferred to Phase 1B.

## Hypothesis epistemic states

```text
PROPOSED
  -> EVIDENCE_SUPPORTED
  -> PREDICTION_REGISTERED
  -> INTERVENTION_READY
  -> INTERVENTION_APPLIED
  -> CORRECTNESS_VERIFIED
  -> PERFORMANCE_VERIFIED
  -> CAUSALLY_SUPPORTED | REJECTED | INCONCLUSIVE
```

`PERFORMANCE_VERIFIED` is not enough for causal support unless the mechanism and
replication requirements in the causal validation protocol pass.
