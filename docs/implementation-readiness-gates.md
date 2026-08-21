# Implementation readiness gates

This document prevents design completeness, synthetic correctness, calibration,
qualification, and real Android reproducibility from being conflated. Phase 1A
closes shared contracts; Phase 1B calibrates them with CPU-001; Phase 1C uses
fresh data to qualify CPU-001; Phase 1D expands the corpus.

## Current evidence

| Problem | What is executable now | What still blocks closure | Status |
|---|---|---|---|
| Data representation | runtime schemas plus Bench reproduction and Agent tool contracts; canonical SHA-256; cross-object invariants; migration policy | first real migration fixture and final schema-bundle versioning | PARTIAL |
| Experiment execution | pure evaluator runs validate → A1 → register → B → A2 → verify → decide with a hash chain | Android build/install/measure/trace runner, crash recovery, actual rollback | PARTIAL |
| Causal validity | preregistration, environment identity, mechanism direction, reversal statistics, and replication are computed | intervention-to-source-factor verifier; clean-build replication; cross-device C2 | PARTIAL |
| Statistical decision | deterministic median effect, bootstrap CI, practical threshold, drift and sample gates | invalid-sample ingestion, paired/randomized designs, secondary metrics, multiplicity and empirical threshold calibration | PARTIAL |
| Agent scope | capability and approval manifests describe the boundary | typed tool I/O, runtime policy enforcement, budgets and sandbox adapter | PARTIAL |
| Benchmark leakage | task validator plus clean read-only exporter, digest manifest, forbidden marker/canary scan | network/environment/process isolation; post-run output scan; evaluator principal separation | PARTIAL |
| Five startup tasks | five machine-readable reproduction manifests; CPU honestly `IMPLEMENTED`, four tasks `DRAFT` | no task has a validated APK, trace set, A1/B/A2 pilot, variance report, or independent replay | BLOCKED ON ANDROID LAB |

## P0 acceptance criteria

### G1 — Contract closure

- Every persisted runtime artifact validates against a versioned schema.
- Canonical digest rules and schema migrations have golden tests.
- Cross-object verification rejects dangling IDs, late registration, selective
  exclusion, mixed run IDs, digest mismatch, and artifact/arm mismatch.

### G2 — Execution contract closure

- The runner uses structured executable/argument arrays, never model-provided
  shell strings.
- Every state has typed preconditions, outputs, failure transitions, retry
  classification, mutation flag, and recovery boundary.
- A simulated adapter performs baseline correctness, A1, registration, patch,
  treatment correctness, B, restore, A2, verification, decision, and cleanup.
- Fault injection after every simulated mutating phase proves deterministic
  resume or rollback behavior. Real Android execution is a Phase 1B test of the
  same interface, not a prerequisite for freezing the interface.

### G3 — Causal/statistical closure

- Gate statuses are computed from sealed input artifacts; the Agent cannot
  submit a passing gate as a fact.
- Primary and protected secondary metrics use preregistered inclusion,
  exclusion, effect, uncertainty, drift, and multiplicity policies.
- Synthetic fixtures prove that failed or missing replication cannot produce
  `CAUSALLY_SUPPORTED`. Real replication thresholds are calibrated in Phase 1B
  and tested on fresh data in Phase 1C.
- Claims remain limited to C1 until declared device strata support C2.

### G4 — Agent authority closure

- Every tool has JSON input/output contracts and a policy decision before use.
- Writable paths, command allowlists, time/experiment/patch budgets, approvals,
  and rollback obligations are enforced by code outside the model.
- Protected benchmarks, correctness tests, evaluator files, and policies are
  never writable by the Agent.

### G5 — Leakage contract closure

- A local isolation harness proves that Agent and evaluator can run as separate
  principals or equivalent isolated processes over separate views.
- The harness denies Agent network and constructs its environment from an
  allowlist; unsupported hosts/platforms must fail closed.
- Public export contains no VCS/cache/private material and passes canary scans
  before and after execution.
- Evaluator output reveals bounded reason codes, not hidden expected answers.

### G6 — Task reproduction contract closure

Before any task is calibrated, define a versioned `TaskReproductionPackage`
that requires the artifacts below and rejects cross-use of development,
calibration, qualification, and evaluation data.

### Phase 1D corpus qualification

For CPU, I/O, Binder, scheduling, and GC tasks independently, freeze:

- source and toolchain revisions plus APK/source/trace digests;
- public correctness and Macrobenchmark scenario;
- private reference patch, hidden correctness tests, and mechanism queries;
- A1/B/A2 raw measurements, exclusions, environment snapshots, and variance;
- a replay command and an independent replay result;
- a leakage review confirming names, comments, labels, and constants do not
  reveal the hidden answer beyond the declared task interface.

Scheduling and GC tasks may be removed or redesigned after pilots if their
within-session variance prevents the preregistered power/effect requirements.
Keeping five task names is not more important than reproducibility.

## Advancement rules

1. **Enter Phase 1B only when G1–G6 contracts pass without Android data.**
2. Phase 1B CPU-001 artifacts are permanently marked `CALIBRATION`; changing a
   threshold or protocol invalidates them for qualification.
3. Enter Phase 1C only after the calibrated policy and task version are frozen.
4. Phase 1C uses fresh sessions and produces the first publishable result.
5. Enter Phase 1D only after CPU-001 independently replays and passes leakage
   review. Other task implementations may not weaken the shared contract.
