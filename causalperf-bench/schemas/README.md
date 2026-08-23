# Benchmark schemas

- `public-task.schema.json` is distributed to the Agent.
- `private-ground-truth.schema.json` is available only to the evaluator.
- `evaluation-result.schema.json` defines the evaluator output.
- `private-canary-set.schema.json` defines task-bound secrets used only for
  leakage detection.
- `isolation-policy.schema.json`, `isolation-run.schema.json` and
  `isolation-report.schema.json` define the fail-closed evaluation boundary.
  Their v2 contracts add Windows drive-letter paths and Windows Sandbox; the
  immutable v1 contracts are retained under `archive/` for migration checks.
- `task-reproduction-package.schema.json` v2 binds every artifact to an
  experimental partition. Its archived v1 remains locked; migration never
  fabricates fresh qualification evidence.

Public and private objects must be packaged separately. `protected_paths` is an
integrity control, not a confidentiality boundary.

Schemas use JSON Schema Draft 2020-12. YAML task files are accepted only after
parsing to JSON-compatible values and validation against the relevant schema.
