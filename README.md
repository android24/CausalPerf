# CausalPerf

> Evidence is observed. Causality is tested.

CausalPerf is an Android performance engineering project focused on a problem
that trace-analysis tools alone cannot solve: determining whether a suspected
bottleneck is actually causal and whether a proposed optimization produces a
correct, reproducible improvement.

Instead of stopping at a performance report, CausalPerf targets the complete
optimization loop:

```text
Regression
    -> Reproduction
    -> Evidence collection
    -> Falsifiable hypothesis
    -> Controlled intervention
    -> Rebuild and benchmark
    -> Correctness verification
    -> Statistical validation
    -> Accept or roll back
```

## Why CausalPerf?

The Android performance ecosystem already has strong tools at every individual
stage:

- Android Vitals and Firebase Performance detect regressions in production.
- Macrobenchmark provides repeatable measurement and trace capture.
- Perfetto and SmartPerfetto provide deep trace analysis and evidence-backed
  explanations.
- Coding agents can inspect repositories and propose source changes.

The unresolved problem is the execution gap between observing a regression and
proving that a safe code change fixes it. CausalPerf connects these stages with
recorded environments, falsifiable predictions, controlled interventions,
correctness gates, repeated measurements, and reversible patches.

CausalPerf does not attempt to replace Perfetto or compete on trace-viewer
features. Perfetto-compatible analysis is one evidence source inside a larger
performance-engineering loop.

## Projects

### [CausalPerf Bench](causalperf-bench/README.md)

A ground-truth benchmark for Android performance diagnosis and optimization
agents. It provides reproducible defects, benchmark scenarios, Perfetto traces,
causal labels, correctness tests, expert fixes, and an evaluator.

The startup benchmark roadmap includes:

- main-thread CPU-intensive work;
- synchronous file I/O;
- Binder blocking;
- CPU scheduling starvation;
- GC and allocation pressure;
- ContentProvider initialization;
- third-party SDK initialization;
- Compose first-frame work;
- mixed-cause startup regressions.

### [CausalPerf Agent](causalperf-agent/README.md)

A closed-loop Android performance optimization agent. Its product-facing name
may be **Performance Doctor**, while CausalPerf Agent remains the technical and
research name.

The agent reproduces regressions, analyzes cross-layer evidence, proposes
falsifiable hypotheses, applies controlled source or configuration changes, and
verifies improvements with repeated benchmarks and functional tests.

## Relationship

```text
CausalPerf Bench
    |-- benchmark apps and known defects
    |-- ground-truth causes and expert patches
    |-- correctness and performance tests
    `-- scoring and trajectory audit
                         |
                         v
CausalPerf Agent
    |-- reproduce and collect evidence
    |-- diagnose and design experiments
    |-- modify, build, install, and rerun
    `-- verify or roll back
                         |
                         v
CausalPerf Bench evaluator
```

CausalPerf Bench is the experimental environment and evaluation standard.
CausalPerf Agent is the system being developed and evaluated.

## End-to-end example

```text
Input
  App 3.8.0 cold-start P90 regressed by 18% on Android 14 devices.

Reproduction
  Rebuild 3.7.0 and 3.8.0, select a representative device, normalize
  compilation state, and run the same cold-start Macrobenchmark.

Observation
  Version 3.8.0 shows 511 ms of main-thread Binder waiting during startup.

Hypothesis and prediction
  A PackageManager query introduced in 3.8.0 materially delays first frame.
  Deferring that query should reduce Binder wait and TTID without changing the
  visible startup result.

Intervention
  Apply a minimal, reviewable patch that moves the query after first frame.

Verification
  TTID median: 1812 ms -> 1431 ms
  Absolute effect: -381 ms
  Relative effect: -21.0%
  Correctness tests: passed
  Environment validity: passed

Output
  Hypothesis: CAUSALLY_SUPPORTED
  Patch: ready for human review
  Experiment ledger: complete
```

## Design principles

1. **Perfetto provides facts; reasoning interprets them.**
2. **Correlation is not promoted to causation without an intervention.**
3. **Every diagnostic claim references inspectable evidence.**
4. **Every optimization must preserve functional correctness.**
5. **Performance improvements require repeated, controlled measurement.**
6. **Every mutation is reviewable and reversible.**
7. **Missing evidence produces an inconclusive result, never a fabricated fact.**
8. **Predictions are registered before treatment results are observed.**
9. **Benchmarks and correctness tests are protected from agent modification.**

## Scope and non-goals

The initial scope is Android cold-start performance on controlled local devices
or emulators. TTID is the primary metric; TTFD and protected secondary metrics
are added when a task defines them.

The initial project does not:

- replace the Perfetto UI or build another general trace chat interface;
- treat a single trace or a fixed threshold as causal proof;
- support Startup, Jank, ANR, Memory, Power, and Thermal simultaneously;
- allow unrestricted source, benchmark, or test modification;
- merge, publish, or deploy an agent-generated patch without human review;
- claim that one device configuration represents the complete production fleet.

## System contracts

The shared conceptual input is a `PerformanceTask`:

```text
PerformanceTask
├── performance goal or regression description
├── source repository and permitted mutation scope
├── application ID, build variant, and launch scenario
├── device and Android environment constraints
├── performance benchmark
├── protected correctness tests
└── optional production or CI regression metadata
```

The shared output is a `PerformanceExperimentResult`:

```text
PerformanceExperimentResult
├── diagnosis and evidence bundle
├── registered hypotheses and predictions
├── intervention plan and source diff
├── baseline and treatment measurements
├── correctness and environmental-validity results
├── statistical verdict
├── accepted patch or rollback result
└── complete experiment ledger
```

## Roadmap

The first phase closes the experimental method before scaling Android task
implementation. A task is never allowed to define shared rules retroactively.

### Phase 1A — Experimental Contract Closure

- Freeze the minimum artifact model, canonical digests, references, and schema
  migration rules.
- Freeze experiment transitions, retry/recovery/rollback semantics, causal
  levels, statistical policy, Agent authority, and leakage isolation.
- Verify them using synthetic pass/fail/inconclusive and fault-injection cases.

No Android result produced in Phase 1A is a benchmark result.

### Phase 1B — CPU-001 Calibration Pilot

- Use CPU-001 only to test measurability, variance, sample count, environment
  limits, mechanism evidence, runner recovery, and sandbox enforcement.
- Permit protocol changes, but label every resulting artifact `CALIBRATION`.
- Prohibit calibration observations from final qualification or Agent scoring.

### Phase 1C — CPU-001 Qualification

- Freeze protocol and task version before collecting fresh data.
- Rebuild and collect new qualification sessions with the reference patch.
- Require independent replay and leakage review before publishing CPU-001.

### Phase 1D — Startup v0.1 Corpus Expansion

- Implement I/O, Binder, scheduling, and GC tasks from the frozen contract.
- Qualify each task independently; redesign or remove unstable tasks.
- Only then evaluate CausalPerf Agent across the five-task corpus.

### Phase 2 — Source-aware optimization

- Map runtime evidence to source locations and commits.
- Produce minimal, reviewable patches.
- Rebuild, install, rerun, verify, and roll back automatically.

### Phase 3 — Performance regression investigator

- Accept regressions from CI or production telemetry.
- Select representative devices and scenarios.
- Reproduce regressions and return verified fixes or an evidence-backed
  inconclusive result.

## Repository layout

```text
CausalPerf/
├── README.md
├── docs/                     # project-wide vision, architecture, roadmap
├── shared/                   # contracts and deterministic code used by both
│   ├── docs/
│   ├── schemas/
│   └── reference/
├── causalperf-bench/
│   ├── README.md
│   ├── schemas/
│   └── tasks/startup/
└── causalperf-agent/
    ├── README.md
    └── docs/
```

The intended implementation is a monorepo until shared contracts and execution
boundaries stabilize. Agent and Bench can be split into separate repositories
later without changing their public schemas.

## Detailed design

- [Architecture](docs/architecture.md)
- [Glossary](docs/glossary.md)
- [Repository ownership map](docs/repository-ownership.md)
- [Data contracts](shared/docs/data-contracts.md)
- [Schema versioning and canonical digests](shared/docs/schema-versioning.md)
- [Causal validation protocol](shared/docs/causal-validation-protocol.md)
- [Experiment execution protocol](shared/docs/experiment-execution-protocol.md)
- [Measurement policy](shared/docs/measurement-policy.md)
- [Agent security and execution boundaries](causalperf-agent/docs/security-and-execution-boundaries.md)
- [Benchmark leakage threat model](causalperf-bench/docs/leakage-threat-model.md)
- [Repository strategy](docs/repository-strategy.md)
- [Runtime schemas](shared/schemas/)
- [Deterministic statistical and causal reference](shared/reference/README.md)
- [Benchmark schemas](causalperf-bench/schemas/)
- [Agent module interfaces](causalperf-agent/docs/module-interfaces.md)
- [Agent execution state machine](causalperf-agent/docs/execution-state-machine.md)
- [Agent approval model](causalperf-agent/docs/approval-model.md)
- [Phase 1A work packages](docs/phase-1a-experimental-contract-closure.md)

## Status

Concept and scope are complete; executable detailed design is in progress. The
first CPU startup task is an unbuilt implementation probe, not a frozen
benchmark. No runnable Agent, trace corpus, statistically calibrated task, or
production evaluator is present yet.

```text
D0 Concept and scope                 COMPLETE
D1 Executable data contracts         IN PROGRESS (19 schemas + invariants)
D2 Experiment execution protocol     IN PROGRESS (pure reference evaluator)
D3 Causal decision engine            IN PROGRESS (computed core gates)
D4 Statistical verifier              IN PROGRESS (synthetic A1/B/A2)
D5 Agent capability contracts        IN PROGRESS
D6 Leakage threat model/auditor       IN PROGRESS (validator + clean exporter)
D7 Reproducible Android task corpus   NOT STARTED
```

“In progress” is deliberate: reference and synthetic execution does not count
as Android validation. The exact remaining entry gates are tracked in
[Implementation readiness gates](docs/implementation-readiness-gates.md).

The active milestone is **Phase 1A — Experimental Contract Closure**. Its
device-independent loop validates:

```text
Task -> Evidence -> Registered prediction -> A1/B/A2 measurements
     -> Integrity/correctness/environment gates
     -> Statistical verdict -> Causal decision -> Ledger
```

CPU-001 calibration and all remaining Android tasks are intentionally paused
until every Phase 1A exit gate passes.

## Program-level acceptance criteria

CausalPerf reaches its first integrated milestone when:

1. all five Startup v0.1 tasks reproduce reliably on the declared device;
2. their hidden causal labels and reference interventions are independently
   verified;
3. the evidence engine analyzes the resulting real traces without fabricated
   zeroes or unsupported claims;
4. the agent registers a prediction before applying an intervention;
5. correctness, environment, and statistical gates run automatically;
6. the evaluator distinguishes correct optimization from deleted work,
   weakened tests, and benchmark-specific shortcuts;
7. every run produces a replayable experiment ledger.
