# Computed causal gates

## Purpose

This contract prevents an Agent or experiment caller from turning an assertion
such as `{"status": "PASS"}` into causal evidence. The reference evaluator
accepts sealed facts and computes every gate used by the C1 decision.

This is a device-independent Phase 1A contract. It does not claim that a JSON
digest authenticates who produced an artifact. Producer authority, protected
workspace isolation, and evaluator separation are enforced by WP5 and WP6.

## Inputs and ownership

| Input | Required facts | Intended producer | Gate consumer |
|---|---|---|---|
| `SourceManifest` | canonical relative paths, file sizes and SHA-256 values, tree digest, applied-patch digest for treatment | execution adapter | Integrity |
| `IntegrityInput` | source manifests, protected paths and frozen policy digest | runner from task policy | Integrity |
| `CorrectnessReport` | suite digest, command digest, exit code, counts, optional behavior digest and raw-result digest | correctness adapter | Correctness |
| `EnvironmentSnapshot` | device identity and raw runtime values | device adapter | Environment |
| `EnvironmentPolicy` | frozen thresholds and permitted states | task owner/evaluator | Environment |
| `InterventionPlan` | planned patch digest, allowed paths, one primary factor and rollback source digest | Agent, then sealed before treatment | Integrity and Isolation |
| measurements/evidence | included raw values tied to source, APK and environment digests | measurement/trace adapters | Statistics and Mechanism |

`CorrectnessReport` intentionally has no status property. Snapshot-local
`validity.status` is diagnostic metadata only; the final Environment Gate is
recomputed from raw fields and the sealed policy.

## Integrity algorithm

1. Verify content digests for the integrity input and every source manifest.
2. Require unique manifest roles and canonical, unique file paths.
3. Recompute each tree digest from its ordered entries.
4. Bind A1, B and A2 measurements to baseline, treatment and restored tree
   digests respectively.
5. Match the baseline tree to the registered rollback source and the applied
   patch artifact to the preregistered patch digest.
6. Diff baseline and treatment entries. Require at least one changed file, keep
   every change inside `allowed_paths`, and reject any protected-path change.
7. Require the restored manifest to exactly equal the baseline manifest.

Missing manifests or protection policy produce `INCONCLUSIVE`. Digest, scope,
protected-file, patch or restoration violations produce `FAIL`.

## Correctness algorithm

Baseline and treatment must use the same sealed suite. Both command executions
must exit successfully, execute at least one test, and report no failed
assertions. Treatment may not reduce the test count or increase skipped tests.
When a protected behavior digest is emitted, both phases must emit it and the
values must match.

A missing phase or incomplete behavior digest is `INCONCLUSIVE`. Command,
assertion, suite, coverage, skip or behavior changes are `FAIL`.

## Environment algorithm

For every snapshot, recompute validity from API range, ABI, battery, charging,
thermal state, online CPU count, available memory, background load and
compilation mode. Then require one stable device/build/toolchain identity across
the experiment. Any violation is `INCONCLUSIVE`; an environment problem does
not falsify the causal hypothesis.

## Isolation and claim level

An intervention with declared additional factors is not isolated. It may reach
E1 when all experimental gates pass, but it cannot reach C1. C1 additionally
requires preregistration, integrity, correctness, environment validity,
mechanism-direction agreement, the preregistered practical/statistical effect,
and reversal/replication.

Phase 1A emits at most C1. C2 remains unavailable until fresh C1 experiments
replicate across frozen device strata.

## Decision precedence

| Condition | Verdict |
|---|---|
| late registration or integrity failure | `INVALID` |
| missing integrity/correctness/environment/mechanism/statistical evidence | `INCONCLUSIVE` |
| correctness, mechanism, performance or replication prediction fails | `REJECT` |
| treatment passes but replication is missing or intervention is not isolated | `EXPERIMENTALLY_SUPPORTED` / E1 |
| all Phase 1A C1 gates pass | `CAUSALLY_SUPPORTED` / C1 |

The verifier records the sealed input digests and computed-gate digest in the
hash-chained ledger. Caller-supplied fields named `integrity_gate`,
`correctness_gate`, or similar are ignored and have no decision authority.

## Known boundary

Canonical SHA-256 detects later mutation; it is not a signature and does not
prove that the Agent lacked write access when the artifact was created. Before
CPU-001 calibration, WP5 must enforce producer capabilities and WP6 must prove
protected-view isolation. Android adapters must also show that tree, test,
environment, APK and trace digests are captured by the runner rather than
reported by the model.
