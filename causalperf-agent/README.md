# CausalPerf Agent

> Diagnose. Intervene. Measure. Prove.

CausalPerf Agent is a closed-loop Android performance optimization agent. Its
product-facing name may be **Performance Doctor**.

Unlike a trace-report assistant, the agent does not treat correlation in one
trace as proof of root cause. It promotes a hypothesis to causally supported
only after a controlled intervention produces a correct and reproducible
performance improvement.

## Target workflow

```text
Performance goal or regression
    -> Reproduce under a recorded environment
    -> Collect traces and benchmark measurements
    -> Extract deterministic evidence
    -> Generate competing, falsifiable hypotheses
    -> Design the smallest safe intervention
    -> Apply a reviewable source or configuration change
    -> Build, install, and rerun
    -> Run correctness gates
    -> Run statistical performance gates
    -> Accept, reject, or roll back
```

## Inputs

The initial agent contract accepts:

```text
PerformanceGoal
SourceRepository
PermittedMutationScope
BuildCommand
ApplicationId
LaunchScenario
DeviceTarget
BenchmarkCommand
CorrectnessCommand
OptionalRegressionMetadata
```

The agent must validate that the build, benchmark, correctness test, device,
and writable scope are available before proposing source changes.

## Outputs

Every completed or inconclusive run produces:

```text
DiagnosisReport
EvidenceBundle
RegisteredHypotheses
ExperimentPlan
SourcePatch or NoPatch
MeasurementComparison
CorrectnessResult
EnvironmentValidityResult
StatisticalVerdict
RollbackResult
ExperimentLedger
```

An inconclusive run is a valid output when the problem cannot be reproduced,
evidence is missing, environmental noise is excessive, or competing hypotheses
cannot be separated safely.

## Planned components

```text
causalperf-agent/
├── evidence-engine/       # Perfetto and Android evidence extraction
├── reproduction/          # Device and scenario control
├── hypothesis-engine/     # Competing hypotheses and predictions
├── experiment-planner/    # Minimal controlled interventions
├── intervention-runner/   # Reviewable and reversible changes
├── benchmark-runner/      # Macrobenchmark orchestration
├── statistical-verifier/  # Baseline/treatment validation
├── correctness-gates/     # Functional and semantic preservation
├── experiment-ledger/     # Complete audit trail
└── reporting/             # Evidence, patch, effect, and risk report
```

## Hypothesis lifecycle

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

`PREDICTION_REGISTERED` is intentionally placed before treatment results are
visible. It records the expected metric direction, affected evidence, minimum
meaningful effect, and observations that would reject the hypothesis. This
prevents post-hoc explanations from being mislabeled as causal tests.

## Verification policy

A patch can be accepted only if:

```text
correctness == PASS
AND task_integrity == PASS
AND environment_validity == PASS
AND effect_size >= task.practical_threshold
AND statistical_verdict == PASS
AND protected_secondary_metrics != REGRESSED
```

Otherwise the patch is rejected, rolled back, or marked inconclusive. Raw
percentage speedup never overrides a failed correctness or integrity gate.

## Safety gates

- No source modification before a measurable hypothesis and prediction exist.
- Every change is shown as a diff and remains reversible.
- Correctness failures invalidate a speedup.
- One unusually fast run cannot establish an improvement.
- Missing or unstable measurements produce `INCONCLUSIVE`.
- AndroidX result JSON and per-iteration Perfetto traces are sealed only when
  metric, iteration and trace identities agree.
- Raw device serials are injected only at transport time and never enter the
  durable benchmark ToolRequest.
- Agent-generated patches require human review before publication or merge.
- Benchmark, correctness, and Ground Truth files are outside the mutation scope.
- A hypothesis records evidence that would falsify it, not only evidence that
  supports it.

## Initial scope and non-goals

The first agent targets controlled Android cold-start tasks from CausalPerf
Bench Startup v0.1. It does not initially:

- diagnose every Android performance category;
- replace SmartPerfetto or the Perfetto UI;
- autonomously merge or deploy patches;
- mutate build infrastructure, benchmarks, or correctness tests;
- generalize results from one device without recording that limitation;
- confirm a causal root cause using only threshold-based trace evidence.

## Initial acceptance criteria

The first usable Agent milestone requires:

1. execution against all five CausalPerf Bench Startup v0.1 tasks;
2. a complete evidence bundle with no unsupported RCA claims;
3. at least one registered falsifiable prediction per attempted intervention;
4. reviewable diffs limited to authorized application paths;
5. automatic build, install, correctness, benchmark, and rollback handling;
6. automatic baseline-versus-treatment statistical comparison;
7. a complete ledger sufficient to replay and audit the run;
8. explicit `REJECTED` or `INCONCLUSIVE` outcomes when proof is insufficient.

## Status

The full Android install, benchmark and trace-evidence adapter chain is not yet
implemented. Phase 1B now includes a read-only Android laboratory preflight
that resolves an explicit device, verifies the pinned host toolchain, samples
device/environment state, hashes identifying values, and emits a schema-valid
`EnvironmentSnapshot` or a fail-closed `INCONCLUSIVE` result. The
preflight is enforced at the state machine's `PREPARING_ENVIRONMENT` boundary,
so an invalid environment cannot reach baseline build or trigger a Wrapper
download. CPU-001 also has a task-local cross-platform Gradle Wrapper and an
SDK-free static drift gate.

The first guarded Android execution slice is implemented behind injectable
transports. `GradleBuildAdapter` runs an exact Wrapper argument vector without
a shell, requires `clean`, binds source/command/toolchain/APK identities,
rejects path escape and symlinks, detects source mutation during build, and
emits schema-valid `BuildResult` and APK artifacts. Policy denial prevents the
transport call. POSIX `gradlew` and Windows `gradlew.bat` remain task-relative
and are selected by host configuration. `CorrectnessReportParser` converts raw
JUnit XML into a sealed, caller-status-free correctness report; malformed,
unsafe, empty, timed-out or truncated results cannot become PASS. These
components are SDK-free tested with fake transports, so their existence is not
evidence of a successful real Android build or test run.

The second guarded slice adds a hashed-device-bound ADB installer, explicit
package cleanup, sealed Gradle correctness execution with bounded JUnit
collection, and a public-only dry-run coordinator. Install uses the released
`install_apk` policy capability; correctness and uninstall remain trusted
Runner operations. A failed install still triggers cleanup, and cleanup failure
vetoes a passing public correctness result. The coordinator deliberately cannot
read or execute the private hidden suite.

The device-independent execution core includes a typed state machine, Adapter
protocol, simulated A1/B/A2 flow, hash-chained intents and
completions, persisted recovery snapshots, rollback behavior, fault injection,
bounded transport retry, and a fail-closed Runtime Policy Engine. The policy
layer enforces typed capabilities, exact approvals, command/path/device scope,
immutable budgets, pre-mutation rollback obligations and ToolCall audit records
outside the model. OS isolation and the Phase 1A contracts are complete, but
the preflight alone is not a runnable Android optimization Agent.

## Design documents

- [Module interfaces](docs/module-interfaces.md)
- [Execution state machine](docs/execution-state-machine.md)
- [Approval model](docs/approval-model.md)
- [Capability manifest](docs/capability-manifest.yaml)
- [Tool contracts](docs/tool-contracts.md)
- [Runtime Policy Engine](docs/runtime-policy-engine.md)
- [Security and execution boundaries](docs/security-and-execution-boundaries.md)
- [Phase 1B CPU-001 calibration plan](../docs/phase-1b-cpu-001-calibration.md)
- [Android SDK late-binding contract](../docs/android-sdk-late-binding.md)
- [Android execution adapters](docs/android-execution-adapters.md)

Cross-project artifact schemas and causal/statistical reference logic live in
[`../shared`](../shared/README.md); they are dependencies of the Agent, not
Agent-owned implementations.
