# Architecture

## Context

CausalPerf connects performance observation, controlled experimentation, and
verified source optimization. Perfetto is an evidence provider, not the system
orchestrator.

```text
Production/CI signal or Bench task
                |
                v
        Task and policy loader
                |
                v
    Reproduction + environment controller
                |
                v
    Benchmark runner + evidence engine
                |
                v
 Hypothesis engine + experiment planner
                |
          approval boundary
                |
                v
 Intervention runner + build/install
                |
                v
 Correctness + integrity + measurement gates
                |
                v
 Statistical verifier + decision engine
                |
                v
 Patch / rollback / inconclusive report
```

## Trust boundaries

```text
Agent sandbox
  can read: public task, source, permitted evidence
  can write: declared source paths and run workspace
  cannot read: private Ground Truth
  cannot write: benchmark, evaluator, correctness tests, policies

Evaluator
  can read: public task, private Ground Truth, immutable run artifacts
  can write: evaluation result only

Device runner
  can execute: allowlisted ADB/Gradle operations for one resolved device
  cannot select: an unspecified device or destructive command target
```

## Components

| Component | Responsibility | Must not do |
|---|---|---|
| Task loader | Validate schemas, resolve paths and budgets | Execute task commands |
| Environment controller | Resolve device and record state | Infer missing policy silently |
| Reproduction runner | Establish a stable baseline | Modify source |
| Evidence engine | Produce normalized observations | Declare causal truth |
| Hypothesis engine | Generate competing mechanisms and falsifiers | Apply changes |
| Experiment planner | Select minimal intervention and prediction | Observe treatment results before registration |
| Intervention runner | Apply approved reversible diff | Modify protected paths |
| Correctness/integrity gates | Preserve work and task validity | Reward speedup |
| Statistical verifier | Compare registered baseline/treatment sets | Ignore invalid environment samples |
| Decision engine | Accept, reject, roll back, or mark inconclusive | Override failed gates |
| Ledger | Preserve replayable audit history | Store secrets or mutable Ground Truth |

## Data flow invariants

1. Every artifact has a content digest and producer.
2. Evidence references immutable source artifacts and query versions.
3. Prediction registration precedes treatment execution.
4. Private evaluator data never enters an agent prompt or workspace.
5. Correctness and integrity failures dominate performance results.
6. A completed run can be replayed from task version, source revision,
   environment manifest, commands, approvals, and artifacts.

## Validation phases and artifact eligibility

| Phase | Purpose | Protocol mutable? | Eligible for benchmark claims? |
|---|---|---:|---:|
| DEVELOPMENT | debug schemas, runner and queries | yes | no |
| CALIBRATION | estimate variance and tune preregistered policy | yes | no |
| QUALIFICATION | validate a frozen task and reference intervention | no | yes |
| EVALUATION | score an Agent against frozen public/private packages | no | yes |

An artifact records exactly one phase. Digests observed in DEVELOPMENT or
CALIBRATION cannot be reused as QUALIFICATION measurements. If qualification
causes a policy change, increment the policy/task version and return to
CALIBRATION.

The first implementation remains local-only and single-agent. Device execution
and source mutation require explicit user approval. Distributed device farms,
production telemetry connectors, autonomous PR creation, and multi-agent
execution are later extensions.
