# Benchmark schemas

- `public-task.schema.json` is distributed to the Agent.
- `private-ground-truth.schema.json` is available only to the evaluator.
- `evaluation-result.schema.json` defines the evaluator output.

Public and private objects must be packaged separately. `protected_paths` is an
integrity control, not a confidentiality boundary.

Schemas use JSON Schema Draft 2020-12. YAML task files are accepted only after
parsing to JSON-compatible values and validation against the relevant schema.

