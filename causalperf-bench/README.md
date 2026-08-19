# CausalPerf Bench

> A ground-truth benchmark for Android performance optimization agents.

CausalPerf Bench evaluates whether an agent can diagnose, fix, and verify real
Android performance problems without changing intended application behavior.

Each benchmark is packaged into two physically separated artifacts:

```text
public-task/                         private-evaluator/
├── app/                             ├── ground-truth.json
├── macrobenchmark/                  ├── expert-patch.diff
├── correctness/                     ├── hidden-tests/
├── traces/                          └── evaluator-policy.json
├── environment/
└── task.yaml
```

The Agent receives only `public-task`. Protected paths prevent mutation, while
separate packaging prevents confidential Ground Truth from being read.

## Benchmark task contract

A task separates information available to the agent from hidden evaluation
truth. A simplified contract looks like this:

```yaml
schema_version: 1
id: startup-main-thread-io-001
version: 0.1.0
category: startup

platform:
  min_api: 31
  max_api: 35
  device_class: physical

target:
  primary_metric: timeToInitialDisplayMs
  startup_mode: COLD
  practical_threshold:
    relative_percent: 10
    absolute_ms: 50
    combination: maximum

source:
  revision: 0123456789abcdef
  application_id: dev.causalperf.startup.io
  build_variant: benchmarkRelease

commands:
  build:
    executable: ./gradlew
    args: [assembleBenchmarkRelease]
    working_directory: .
    timeout_seconds: 1200
  correctness: { executable: ./gradlew, args: [connectedCheck], working_directory: ., timeout_seconds: 1200 }
  performance: { executable: ./gradlew, args: [":benchmark:connectedBenchmark"], working_directory: ., timeout_seconds: 3600 }

measurement:
  design: a1_b_a2
  stabilization_iterations: 3
  measurement_iterations_per_arm: 30
  compilation_mode: none
  bootstrap_resamples: 10000
  confidence_level: 0.95
  max_invalid_sample_percent: 10
  max_baseline_drift_percent: 10

agent_access:
  writable_paths:
    - app/src/main
  protected_paths:
    - benchmark
    - correctness
  network: denied

budgets:
  wall_time_seconds: 14400
  max_experiments: 5
  max_patch_files: 8
```

The executable contract is
[`schemas/public-task.schema.json`](schemas/public-task.schema.json). Private
truth is validated separately and is never embedded in this public object.

## Ground truth model

Ground truth is not a single expected patch. Every task records:

```text
GroundTruth
├── InjectedFault             # what was deliberately made inefficient
├── CausalMechanism           # why it changes the target metric
├── ObservableEvidence        # expected runtime manifestations
├── ValidInterventions        # classes of acceptable corrections
└── ExpertReferencePatch      # one validated solution, not the only solution
```

An alternative agent patch may pass if it preserves required behavior, removes
the causal mechanism, and satisfies the measurement gates. Exact textual
similarity to the expert patch is not required.

## Initial evaluation dimensions

- root-cause Top-1 and Top-3 accuracy;
- evidence precision and unsupported-claim rate;
- patch correctness;
- reproducible performance improvement;
- benchmark exploitation and semantic-shortcut detection;
- number of experiments and tool calls;
- wall-clock and model cost;
- trajectory auditability.

## Evaluation gates

A speedup is valid only when all applicable gates pass:

```text
Task integrity
    AND build success
    AND functional correctness
    AND semantic-work preservation
    AND environmental validity
    AND practical performance improvement
    AND statistical reliability
```

The evaluator rejects or invalidates results that:

- modify protected benchmark, correctness, or ground-truth files;
- remove required work, screens, data, or user-visible behavior;
- hard-code benchmark inputs or detect the benchmark runner;
- disable instrumentation needed to verify the task;
- report an improvement from an invalid thermal or compilation state;
- rely on one unusually fast iteration.

## Measurement policy

Every task records its policy rather than relying on one universal threshold.
The default Startup v0.1 policy will specify:

- controlled cold-start state and compilation mode;
- warmup and measured iteration counts;
- median and P90 summaries;
- absolute and relative effect size;
- bootstrap confidence interval;
- practical significance threshold;
- thermal, battery, and background-load validity checks;
- protected secondary metrics and permitted regression bounds;
- explicit flaky or inconclusive outcomes.

Exact statistical thresholds will be calibrated with pilot variance data before
the benchmark is frozen.

## Data partitions

Every task uses four non-overlapping artifact roles:

| Partition | Purpose | May tune policy? | May score Agent? |
|---|---|---:|---:|
| Development | debug implementation and collectors | yes | no |
| Calibration | estimate variance and freeze thresholds | yes | no |
| Qualification | validate frozen task/reference patch | no | no |
| Evaluation | hidden Agent assessment | no | yes |

Content digests are recorded in a partition registry. Reusing a Development or
Calibration measurement in Qualification or Evaluation invalidates the task
version. Qualification results may approve a frozen task but cannot tune it.

## Initial milestone

The first milestone is **CausalPerf Bench Startup v0.1**, containing five
single-cause cold-start tasks:

1. main-thread CPU-intensive work;
2. synchronous main-thread file I/O;
3. synchronous Binder blocking;
4. CPU scheduling starvation;
5. allocation pressure and GC pauses.

Mixed-cause tasks are introduced only after the single-cause tasks are stable
and reproducible across the selected device matrix.

### Startup v0.2

- ContentProvider initialization;
- third-party SDK initialization;
- Compose first-frame work.

### Startup v0.3

- mixed-cause startup regressions;
- competing explanations and partially effective interventions.

## v0.1 acceptance criteria

Startup v0.1 is complete when:

1. five tasks build and pass their correctness tests before intervention;
2. each task shows a reproducible regression above its practical threshold;
3. each expert patch passes correctness tests and reproducibly improves the
   target metric;
4. traces and environment manifests are captured with pinned tool versions;
5. hidden Ground Truth is inaccessible to the evaluated agent;
6. the evaluator detects protected-file edits and semantic shortcuts;
7. a baseline agent run can be replayed and scored end to end.

Before these corpus-level criteria, CPU-001 passes three separate gates:
contract closure, calibration, and fresh-data qualification. The other four
tasks do not enter implementation merely because CPU-001 calibration runs.

## Status

Detailed design. Public/private schemas and five task specifications are
present. `startup-main-thread-cpu-001` has its first Android/Macrobenchmark
implementation and machine validator, but has not been built or piloted on a
device. The other applications, executable private evaluator, pilot
measurements, and trace corpus remain to be created.

## Design documents

- [Leakage threat model](docs/leakage-threat-model.md)
- [Benchmark schemas](schemas/README.md)

Cross-project runtime artifacts and causal/statistical semantics live in
[`../shared`](../shared/README.md). Bench owns public/private task packaging,
qualification data, isolation, Ground Truth, scoring, and evaluator behavior.
