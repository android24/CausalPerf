# Phase 1A — Experimental Contract Closure

## Objective

Resolve the seven foundational questions as executable contracts before a real
Android pilot is allowed to tune them. Phase 1A produces no performance claim
and consumes no qualification data.

## Work packages

| ID | Question | Required deliverable | Machine acceptance |
|---|---|---|---|
| WP1 | How is data represented? | complete artifact schemas, canonicalization, migration and partition registry | schema/invariant/golden/migration tests pass |
| WP2 | How is an experiment executed? | typed state transition table, command envelope, adapter API, recovery plan | simulated pass/fail/crash at every state |
| WP3 | When is causality supported? | preregistration, minimal-intervention, mechanism, reversal/replication and claim-level rules | adversarial fixtures cannot self-assert a gate |
| WP4 | How are statistics decided? | primary/secondary metric policies, exclusions, design-specific analysis, uncertainty and multiplicity | deterministic fixtures cover PASS/FAIL/INCONCLUSIVE |
| WP5 | What may the Agent do? | tool JSON contracts, capability/policy engine, approvals, budgets and rollback obligations | unauthorized calls and mutations fail closed |
| WP6 | How is leakage prevented? | clean export, isolation harness, environment/network policy and pre/post canary scan | seeded leaks are detected in every declared channel |
| WP7 | How are five tasks reproduced? | one versioned reproduction-package contract and partition registry | five empty manifests validate structurally; missing evidence fails completeness |

## Current work-package status

| ID | Phase 1A status | Evidence | Remaining outside this work package |
|---|---|---|---|
| WP1 | COMPLETE | `causalperf-contracts@0.6.0` lock covers every current and archived Schema; canonical digests, invariants, partition registry and fail-closed v1→v2 isolation/reproduction migrations have golden tests | future semantic changes require the same archived-schema/migration discipline |
| WP2 | COMPLETE | typed controller, simulated adapter, atomic recovery, rollback and fault injection | Android adapters in Phase 1B |
| WP3 | COMPLETE | all C1 gates computed from sealed facts; protected/source/correctness/environment/isolation adversarial tests | producer isolation in WP5/WP6; Android calibration and C2 later |
| WP4 | COMPLETE | preregistered A1/B/A2 primary and protected metrics, exclusion/invalid accounting, deterministic uncertainty, Bonferroni control and adversarial verdict fixtures | empirical limits calibrate in Phase 1B; new designs require versioned estimators |
| WP5 | COMPLETE | sealed runtime policy, immutable typed requests, fail-closed scope checks, trusted approval timing, budget reservation, rollback obligations, ToolCall audit and controller integration have adversarial tests | Phase 1B real execution adapters must preserve the frozen authority boundary |
| WP6 | COMPLETE | sealed isolation policy/run/report and private canary contracts; read-only clean export; split Agent/evaluator views; Linux Bubblewrap, macOS sandbox-exec and Windows Sandbox network/filesystem/process-or-VM boundaries; pre/post workspace/output/log scans; bounded public reasons; seeded adversarial and Darwin host probes | every evaluation host, including Windows, must pass backend conformance; task-specific private scoring arrives with WP7/Phase 1D |
| WP7 | COMPLETE | reproduction v2 binds every artifact to a partition, permits the same evidence kind only across distinct partitions, requires fresh calibration/qualification evidence, rejects digest reuse and supports target-lifecycle completeness checks; five honest manifests and adversarial tests pass | CPU calibration/qualification evidence and four task implementations are Phase 1B–1D work, not Phase 1A contract work |

WP3's algorithms and trust boundary are normative in
[Computed causal gates](../shared/docs/computed-causal-gates.md).

## Dependency order

```text
WP1 data and partition identity
 ├──> WP2 execution and recovery
 ├──> WP3 causal gates
 ├──> WP4 statistical gates
 ├──> WP5 Agent authority
 └──> WP6 isolation and leakage
             |
             v
      WP7 reproduction package
             |
             v
      Phase 1A exit audit
```

WP2–WP6 may be implemented in parallel after WP1 identifiers and digest rules
are frozen, but their exit audit is joint because each consumes artifacts from
the others.

## Normative decisions to freeze

### Representation

- JSON Schema Draft 2020-12, `schema_version`, canonical JSON SHA-256, UTC
  timestamps and explicit units; producer/tool identity is embedded or bound
  by ToolCall, Artifact and ledger references.
- `UNKNOWN`/`UNAVAILABLE` are states, never numeric zero.
- Every measurement references run, partition, arm, environment, source, APK,
  trace/result artifact and inclusion decision.

### Execution

- Model output is a typed request, not a shell command.
- Intent is appended before every mutation; completion follows it.
- Only transport failures without an in-flight mutation receive one automatic
  retry. Performance samples are never selectively retried.
- Uncertain workspace/device state means rollback or `INCONCLUSIVE`, not resume.

### Causality

- Speedup alone cannot exceed E1.
- C1 requires preregistration, integrity, correctness, environment validity,
  mechanism-direction agreement, practical/statistical effect, and reversal or
  replication.
- C2 additionally requires frozen device-stratum replication.
- Multi-factor interventions are ineligible for C1 until isolated.

### Statistics

- Practical and uncertainty gates are independent.
- All exclusions are preregistered and remain visible.
- Primary metric controls acceptance; protected secondary metrics can veto it.
- Analysis method follows the declared design; post-hoc method switching is an
  invalid experiment, not a fallback.

### Agent authority

- The external Policy Engine owns permissions, budgets and approvals.
- The Agent cannot write benchmark, correctness, policy or evaluator material;
  declare its own gate PASS; extend its budget; or suppress failed samples.
- A mutation without accepted verification creates a rollback obligation.

### Leakage

- Authoring, Agent and evaluator views are separate.
- Public export is allowlist-based and strips VCS/cache/private material.
- Agent network is denied and environment allowlisted during evaluation.
- Canary discovery in input/output/log/prompt/ledger invalidates the run.

### Reproduction

- Each task must provide development, calibration, qualification and evaluation
  partition identities without sharing measurements across roles.
- Task completeness and task success are distinct: a structurally complete
  package can still be statistically flaky and must then be redesigned.

## Exit criteria

Phase 1A is complete only when:

1. every WP has a versioned implementation and negative tests;
2. the synthetic runner completes PASS, FAIL, INCONCLUSIVE and crash/recovery
   trajectories without accepting caller-supplied gate truth;
3. a seeded leakage corpus is rejected before and after Agent execution;
4. a reproduction-package completeness checker rejects all missing required
   task evidence;
5. an audit report records exact test commands, versions and known limitations.

Only then may Phase 1B connect CPU-001 to Gradle, ADB, Macrobenchmark and
Perfetto for calibration.

The completed evidence and non-claims are recorded in the
[Phase 1A exit audit](phase-1a-exit-audit.md).
