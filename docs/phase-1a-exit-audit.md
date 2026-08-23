# Phase 1A exit audit

Audit date: 2026-08-23  
Contract release: `causalperf-contracts@0.6.0`  
Bundle digest: `d312557a1d6b07636deac527f36f6c9dbd36dcdee228f26b4daeb5531e5a2e2b`

## Decision

Phase 1A experimental-contract closure is **COMPLETE** when the exact command
below passes. This decision means the representation, execution, causal,
statistical, authority, leakage and reproduction rules are executable and fail
closed. It is not an Android performance result and does not authorize use of
Development or Calibration observations for qualification or Agent scoring.

## Exact verification command

Run from the `CausalPerf/` repository root:

```bash
PYTHONPYCACHEPREFIX=/tmp/causalperf-pycache sh tools/test_all.sh
```

Audited host versions:

```text
Python 3.12.4
git 2.45.2
macOS 26.6 (Darwin 25.6.0 arm64)
JSON Schema Draft 2020-12
```

The audit passes only when repository boundaries and every locked Schema pass,
all unit suites pass, CPU-001 public/private packaging validates, and all five
reproduction manifests report their real lifecycle without overstating missing
evidence.

Observed result for this audit: 37 Schemas validated and 145 tests passed (57
shared-reference, 43 Bench, 41 Agent and 4 repository-boundary tests).
CPU-001 packaging and all five lifecycle manifests also passed their
command-line validators. Repository file counts are intentionally not used as
release evidence because ignored runtime caches can change that diagnostic
number without changing tracked source.

## Exit-criterion evidence

| Criterion | Executable evidence | Result |
|---|---|---|
| WP1 representation | bundle membership/digest, archived v1 Schemas, canonical digests, migration and invariant tests | PASS |
| WP2 execution | simulated A1/B/A2 controller, atomic snapshots, fault injection, rollback and recovery tests | PASS |
| WP3 causality | computed integrity, correctness, environment, mechanism, intervention and reversal/replication gates | PASS |
| WP4 statistics | deterministic practical effect, bootstrap uncertainty, drift, invalid-sample and multiplicity tests | PASS |
| WP5 Agent authority | typed tool contracts, sealed policy, approval binding, budgets, rollback obligations and denial-before-dispatch tests | PASS |
| WP6 leakage | clean export, private canaries, split views, Linux/macOS/Windows backend construction, post-run scans and bounded failure reports | PASS |
| WP7 reproduction | partition-scoped artifact identity, target-lifecycle completeness, fresh qualification requirements and cross-partition reuse rejection | PASS |

The WP7 negative fixture explicitly proves that CPU-001 passes
`--require-lifecycle IMPLEMENTED` and fails
`--require-lifecycle CALIBRATED`. A separate fixture proves a DRAFT task cannot
pass the IMPLEMENTED target. Qualification requires a second, distinct set of
environment, A1/B/A2, trace, mechanism and variance artifacts under the
QUALIFICATION partition.

## Known limitations and non-claims

- Release provenance is the annotated `causalperf-contracts@0.6.0` tag. The
  tag binds this audit, CI configuration and contract bundle; moving or
  recreating the tag invalidates the release audit.
- CPU-001 has source, benchmark, correctness, Ground Truth and reference-patch
  skeletons, but no validated APK, device trace, calibration session, variance
  report or independent replay.
- I/O, Binder, scheduling and GC remain honest DRAFT packages.
- No Phase 1A output supports a performance, causal-effect or Agent-quality
  claim.
- macOS sandbox-exec has host-probe evidence. Linux Bubblewrap and Windows
  Sandbox still require conformance on every scoring host; Windows specifically
  requires Windows 11 24H2, the Windows Sandbox feature and `wsb.exe`.
- Task-specific hidden scoring and semantic-shortcut detection remain Phase 1D
  evaluator work.

The platform qualification checklist is maintained in
[Isolation backend conformance](isolation-backend-conformance.md).

## Advancement boundary

The next allowed phase is Phase 1B CPU-001 Calibration. Every resulting session
must be labeled `CALIBRATION`; thresholds may change there, but none of its
measurements may later appear in QUALIFICATION or EVALUATION. Phase 1C must
freeze the protocol and collect fresh qualification artifacts before making a
publishable CPU-001 claim.
