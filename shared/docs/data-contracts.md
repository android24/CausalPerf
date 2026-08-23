# Data contracts

This document defines logical contracts. Published JSON Schemas under
`shared/schemas/` and `causalperf-bench/schemas/` are the executable structural
source of truth. `shared/reference/causalperf_reference/artifacts.py` enforces
cross-object invariants that JSON Schema cannot express, including digest
integrity, reference consistency, preregistration order, arm completeness,
environment association, and preregistered exclusions.

## Contract rules

- Identifiers are stable strings scoped by artifact type.
- Timestamps are UTC RFC 3339; Perfetto timestamps remain integer nanoseconds.
- Durations use explicit unit suffixes such as `_ms` or `_ns`.
- Every persisted document includes `schema_version` and is either sealed by a
  SHA-256 content digest or referenced as a raw-byte artifact. Producer/tool
  identity is embedded where defined and otherwise bound through its ToolCall,
  Artifact and ledger event; it is never inferred from file location.
- Unknown and unavailable are represented explicitly; missing values are never
  silently converted to zero.

Canonicalization, compatibility, and migration rules are normative in
[Schema versioning and canonical digests](schema-versioning.md).

## Executable artifact inventory

Shared schemas cover environment and its validity policy, evidence, hypothesis,
prediction, intervention, source/integrity manifests, raw correctness reports,
measurement sets, generic artifacts, build results, tool calls, approvals, gate
results, rollback results, ledger events, experiment records, final experiment
results, and the data-partition registry. Benchmark-specific
public task, private Ground Truth, and evaluation-result schemas remain under
`causalperf-bench/schemas/`.

## PerformanceTask

```text
id, version, category
target metric and practical threshold
source revision and permitted writable paths
build, correctness, and benchmark commands
device and environment constraints
measurement policy
protected paths and execution budgets
public artifact digests
```

## EnvironmentSnapshot

```text
device serial hash, model, ABI, API level, build fingerprint
battery level, charging state, thermal status
CPU topology and online cores
free storage and memory pressure indicators
compilation mode and package dexopt state
background-load indicators
toolchain versions
capture timestamp and validity violations
```

Raw device serials and credentials must not be persisted.

## Evidence

```text
id
category and metric name
typed value and unit
process/thread identity when applicable
start/end timestamp
source artifact digest
query/collector ID and version
confidence and validity flags
metadata
```

Evidence proves an observation, not causality.

## Hypothesis and prediction

```text
Hypothesis
├── id and proposed mechanism
├── supporting and contradicting evidence IDs
├── alternative explanations
├── epistemic state
└── registered prediction IDs

Prediction
├── id and registered_at
├── intervention mechanism
├── primary metric and expected direction
├── minimum meaningful effect
├── expected mechanism-evidence change
├── protected secondary metrics
└── falsification conditions
```

`registered_at` must precede the first treatment measurement timestamp.

## InterventionPlan

```text
id, hypothesis_id, prediction_id
intent and expected mechanism
allowed files and proposed diff digest
commands and time/resource budgets
risk classification
rollback procedure
required approval
```

One plan should alter one primary causal factor. Multi-factor changes require an
explicit justification and cannot receive the strongest causal verdict without
follow-up isolation.

## Measurement and MeasurementSet

```text
Measurement
├── run ID, arm, sequence position
├── metric name, value, and unit
├── environment snapshot ID
├── artifact digests
└── inclusion status and exclusion reason

MeasurementSet
├── baseline or treatment arm
├── ordered measurements
├── warmup count
├── included/excluded counts
└── summary statistics
```

Exclusions follow preregistered rules. The Agent cannot discard measurements
because they weaken the desired conclusion.

## GateResult

Every gate returns:

```text
gate ID and version
status: PASS | FAIL | INCONCLUSIVE
machine-readable reason codes
human-readable summary
input and output artifact digests
timestamp
```

Gate results are evaluator outputs, never Agent inputs. Integrity, correctness,
environment, isolation, mechanism, statistics and replication are computed as
specified in [Computed causal gates](computed-causal-gates.md).

## ExperimentRecord and ledger

The ledger is append-only and hash-chained. Each event includes run ID, sequence
number, previous-event digest, actor, action, inputs, outputs, approval identity,
timestamps, and exit status. Secrets, private Ground Truth, and raw provider
credentials are forbidden ledger content.

## OptimizationDecision

```text
ACCEPT
  all mandatory gates pass and replication requirements are met

REJECT
  prediction is falsified, correctness/integrity fails, or effect is harmful

INCONCLUSIVE
  reproduction, evidence, environment, or measurement is insufficient

ROLLBACK_REQUIRED
  a mutation occurred but acceptance conditions were not satisfied
```
