# Implementation readiness gates

This document prevents design completeness, synthetic correctness, calibration,
qualification, and real Android reproducibility from being conflated. Phase 1A
closes shared contracts; Phase 1B calibrates them with CPU-001; Phase 1C uses
fresh data to qualify CPU-001; Phase 1D expands the corpus.

## Current evidence

| Problem | What is executable now | What still blocks closure | Status |
|---|---|---|---|
| Data representation | Phase 1A contract complete: `causalperf-contracts@0.6.0` locks every current and archived Schema digest; runtime inputs, canonical SHA-256, cross-object invariants, partition registry and fail-closed migration behavior have golden tests; isolation and reproduction contracts have archived v1 and pure v1→v2 migrations | future contract versions require equivalent archived fixtures and contiguous migrations | PHASE_1A_COMPLETE |
| Experiment execution | Phase 1A contract complete: typed controller, Adapter protocol, simulated A1/B/A2 loop, atomic checkpoints, content-addressed snapshots, hash-chain restart, failure/crash injection at every phase, conservative rollback and bounded transport retry | Phase 1B Android build/install/measure/trace adapters and device/workspace rollback | PHASE_1A_COMPLETE |
| Causal validity | Phase 1A contract complete: integrity, correctness, raw environment policy, intervention isolation, preregistration, mechanism direction, reversal statistics and replication are computed; caller PASS assertions are ignored | runner-authenticated Android artifacts, clean-build replication calibration and cross-device C2 | PHASE_1A_COMPLETE |
| Statistical decision | Phase 1A Startup v0.1 contract complete: preregistered A1/B/A2 policy, invalid-sample accounting, directional practical effect, deterministic bootstrap uncertainty, drift, protected-secondary vetoes, Bonferroni family control and descriptive summaries | Phase 1B empirical threshold/sample calibration; randomized or blocked designs require a future contract version | PHASE_1A_COMPLETE |
| Agent scope | Phase 1A authority contract complete: typed tool I/O, sealed Runtime Policy, exact approval binding with trusted time, path/command/device/partition checks, immutable budgets, rollback obligations and contract-valid ToolCall audit are enforced before adapter dispatch | Phase 1B Gradle/ADB/Perfetto adapters must execute only through these frozen boundaries | PHASE_1A_COMPLETE |
| Benchmark leakage | Phase 1A contract complete: read-only clean export, task-bound private canaries, sealed isolation policy/run/report, Linux/macOS/Windows fail-closed backends, separate Agent/evaluator views, network and environment control, owned process/VM lifecycles, post-workspace/output/log/evaluator scans and bounded public reasons have adversarial tests; Darwin network/private-view host probes pass | every evaluation host must pass backend conformance; the Windows implementation still needs a real Windows 11 24H2 host probe; task-specific semantic-shortcut evaluator logic belongs to Phase 1D | PHASE_1A_COMPLETE |
| Five startup tasks | reproduction v2 and target-lifecycle checker prove partition-scoped artifact identity and require distinct calibration/qualification measurements; five manifests validate honestly; CPU is `IMPLEMENTED`, four tasks are `DRAFT` | no task has a validated APK, trace set, A1/B/A2 pilot, variance report, or independent replay | CONTRACT_COMPLETE; DATA_BLOCKED_ON_ANDROID_LAB |

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
