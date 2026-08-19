# Execution state machine

Execution state and hypothesis state are independent.

## Execution states

```text
CREATED
  -> VALIDATING
  -> PREPARING_ENVIRONMENT
  -> REPRODUCING
  -> COLLECTING_BASELINE
  -> DIAGNOSING
  -> PLANNING_EXPERIMENT
  -> AWAITING_APPROVAL
  -> APPLYING_INTERVENTION
  -> BUILDING
  -> VERIFYING_CORRECTNESS
  -> COLLECTING_TREATMENT
  -> VERIFYING_EFFECT
  -> REPLICATING
  -> DECIDING
  -> ROLLING_BACK
  -> COMPLETED
```

Terminal alternatives are `FAILED`, `CANCELLED`, and `ROLLBACK_REQUIRED`.

## Transition rules

| From | To | Required condition |
|---|---|---|
| CREATED | VALIDATING | run ID and public task received |
| VALIDATING | PREPARING_ENVIRONMENT | schema, digest, scope, and budget valid |
| PREPARING_ENVIRONMENT | REPRODUCING | one device resolved; environment valid |
| REPRODUCING | COLLECTING_BASELINE | task behavior and regression reproducible |
| COLLECTING_BASELINE | DIAGNOSING | baseline policy satisfied |
| DIAGNOSING | PLANNING_EXPERIMENT | evidence bundle sealed |
| PLANNING_EXPERIMENT | AWAITING_APPROVAL | prediction registered; plan valid |
| AWAITING_APPROVAL | APPLYING_INTERVENTION | required approval recorded |
| APPLYING_INTERVENTION | BUILDING | protected paths unchanged |
| BUILDING | VERIFYING_CORRECTNESS | build artifact digest recorded |
| VERIFYING_CORRECTNESS | COLLECTING_TREATMENT | correctness and integrity pass |
| COLLECTING_TREATMENT | VERIFYING_EFFECT | treatment policy satisfied |
| VERIFYING_EFFECT | REPLICATING | preliminary practical/statistical gates pass |
| REPLICATING | DECIDING | required reversal/replication complete |
| DECIDING | ROLLING_BACK | patch not accepted or task requires cleanup |
| DECIDING | COMPLETED | accepted patch may remain in review workspace |
| ROLLING_BACK | COMPLETED | baseline restoration verified |

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

