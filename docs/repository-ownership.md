# Repository ownership map

## Rule

Ownership follows who is allowed to change the behavior and who consumes it.
The monorepo root coordinates the program; `shared` defines common language;
Agent performs diagnosis and intervention; Bench owns tasks and scoring.

| Location | Owns | Must not contain |
|---|---|---|
| `docs/` | product scope, architecture, glossary, phase plans, readiness and repository governance | task Ground Truth, Agent-only tool policy, shared executable schemas |
| `shared/docs/` | normative experiment, causal, statistical, measurement and schema protocols | product roadmap, private evaluation rules, provider-specific Agent behavior |
| `shared/schemas/` | artifacts exchanged between Agent, runner and evaluator | private Ground Truth or task-authoring-only formats |
| `shared/reference/` | deterministic, model-independent reference validation consumed by both projects | Android device orchestration, source mutation, private scoring weights |
| `causalperf-agent/` | evidence collection, planning, policy enforcement, tools, Android execution, rollback and reporting | private evaluator, injected-fault labels, reference answers |
| `causalperf-bench/` | public tasks, private Ground Truth, qualification/evaluation corpus, exporters, isolation and scoring | Agent hypothesis/planning implementation or product integrations |

## Current placement

```text
CausalPerf/
├── README.md
├── docs/
│   ├── architecture.md
│   ├── glossary.md
│   ├── implementation-readiness-gates.md
│   ├── phase-1a-experimental-contract-closure.md
│   ├── repository-ownership.md
│   └── repository-strategy.md
├── shared/
│   ├── README.md
│   ├── docs/
│   │   ├── causal-validation-protocol.md
│   │   ├── data-contracts.md
│   │   ├── experiment-execution-protocol.md
│   │   ├── measurement-policy.md
│   │   └── schema-versioning.md
│   ├── schemas/
│   └── reference/
├── causalperf-agent/
│   ├── README.md
│   ├── docs/
│       ├── approval-model.md
│       ├── capability-manifest.yaml
│       ├── execution-state-machine.md
│       ├── module-interfaces.md
│       └── security-and-execution-boundaries.md
│   ├── schemas/             # Agent-only tool request/response contracts
│   └── tests/
└── causalperf-bench/
    ├── README.md
    ├── docs/leakage-threat-model.md
    ├── schemas/
    ├── tasks/
    ├── tools/
    └── tests/
```

## Boundary decisions

- `EnvironmentSnapshot`, `MeasurementSet`, `Prediction`, `GateResult` and
  `ExperimentResult` are shared because Agent produces them and Bench validates
  them.
- `public-task`, `private-ground-truth` and `evaluation-result` are Bench-owned
  because they define task authoring and scoring rather than Agent runtime.
- Approval and ToolCall records remain shared artifacts because the evaluator
  audits them, while capability policy and tool implementations are Agent-owned.
- Concrete tool request/response schemas are Agent-owned because they describe
  execution adapters; the shared `ToolCall` record only preserves their audited
  identity, policy decision and outcome.
- Statistical and causal reference code remains shared. A production Android
  runner will be Agent-owned; a private score calculator will be Bench-owned.

## Future directories

Create directories only when their first executable file exists:

- `causalperf-agent/src/` and `tests/` for production Agent implementation;
- `causalperf-bench/evaluator/` for private scoring;
- `causalperf-bench/qualification/` for frozen qualification manifests;
- `shared/migrations/` when schema version 2 creates the first real migration.

Empty architectural directories are avoided because they can falsely imply an
implemented subsystem.
