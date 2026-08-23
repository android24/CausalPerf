# Shared runtime schemas

These Draft 2020-12 JSON Schemas define artifacts exchanged by CausalPerf Agent,
the experiment runner, and CausalPerf Bench evaluator.

## Ownership

- Agent produces observations, plans, calls, builds, measurements and results.
- Shared deterministic validators compute and verify cross-artifact invariants.
- Bench consumes the artifacts for integrity and scoring.
- Bench-only authoring and private evaluation schemas remain in
  `causalperf-bench/schemas/`.

Structural validity alone is insufficient. Cross-object rules such as digest
integrity, preregistration order, reference consistency, partition isolation,
sample exclusions and approval binding are implemented in
`shared/reference/causalperf_reference/artifacts.py`.

`source-manifest`, `integrity-input`, `correctness-report`, and
`environment-policy` are the raw inputs to computed causal gates. In
particular, `correctness-report` has no caller-selected gate status. See
[Computed causal gates](../docs/computed-causal-gates.md).

Schema evolution follows `shared/docs/schema-versioning.md`. Unknown versions
fail closed; a new semantic version requires an explicit migration.
The exact released set is frozen in `shared/schema-bundle.lock.json` and
verified by the reference test suite.
