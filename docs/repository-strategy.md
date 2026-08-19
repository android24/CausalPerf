# Repository strategy

## Initial decision

Use a monorepo while public contracts are evolving:

```text
CausalPerf/
├── docs/                   # project governance, architecture and roadmap
├── shared/                 # versioned protocols used by Bench and Agent
│   ├── docs/
│   ├── schemas/
│   └── reference/
├── causalperf-bench/
│   ├── docs/
│   ├── schemas/
│   ├── tasks/
│   ├── tools/
│   ├── tests/
│   ├── evaluator/
│   └── private/            # excluded from agent distributions
└── causalperf-agent/
    ├── src/
    ├── docs/
    └── tests/
```

The normative ownership rules are in
[Repository ownership map](repository-ownership.md). A file belongs in
`shared/` only when both Agent and Bench must consume the same versioned
semantics. “Potentially reusable” is not sufficient.

## Public/private separation

Public task packages and private evaluator packages are separate build
artifacts. A protected directory inside the same readable sandbox is not
sufficient isolation.

```text
dist/public-tasks/<task-id>.tar.zst
dist/private-evaluator/<task-id>.tar.zst
```

The agent receives only the public package. CI mounts the private evaluator
after the agent process exits or in a separate security principal.

## Versioning

- Schemas use explicit integer `schema_version` and semantic package versions.
- Frozen benchmark tasks are immutable; corrections create a new task version.
- Toolchain, trace processor, device image, source revision, and benchmark
  dependencies are pinned in the environment manifest.
- Evaluations report both benchmark version and evaluator version.

## Split criteria

Agent and Bench may become separate repositories only after:

1. public schemas have at least one backward-compatible released version;
2. end-to-end CI validates cross-repository compatibility;
3. private Ground Truth packaging is operational;
4. shared code can be released as a versioned package without path coupling.
