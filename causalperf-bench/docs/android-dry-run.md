# Android task dry-run contract

## Purpose

`AndroidDryRunResult` is development evidence that one Android benchmark task
can pass its pre-calibration execution gates. It is not a performance result,
does not enter `CALIBRATION`, and cannot qualify a task or an Agent.

The contract belongs to CausalPerf Bench because it combines task-authoring,
public correctness and evaluator-only correctness facts. Runtime adapters emit
the raw facts; the deterministic Bench validator computes the final status.

## Ordered gates

```text
static task/toolchain validation
    -> Android laboratory preflight
    -> clean baseline build
    -> public correctness
    -> evaluator-only hidden correctness
    -> clean restored build
    -> source/APK identity comparison
    -> computed PASS | FAIL | INCONCLUSIVE
```

Every command, suite, source tree, APK and raw result is represented by a
SHA-256 identity. `artifact_digests` must be exactly the set referenced by the
record; an omitted or unrelated digest invalidates the contract.

## Honest partial execution

Build and correctness steps carry `execution_status: EXECUTED | NOT_RUN`.
`NOT_RUN` requires a reason and forbids exit codes, APKs, counts and result
artifacts. This permits an environment with no SDK or device to record a real
`INCONCLUSIVE` outcome without fabricating downstream evidence.

The validator rejects a build executed after static validation or preflight
failed, and rejects correctness executed without a successful baseline build.
An inconclusive preflight may omit `environment_snapshot_sha256` because host
tool failures occur before a complete snapshot exists; its raw preflight result
digest remains mandatory.

## Computed outcome

`FAIL` has precedence when the record proves a semantic or protocol violation:

- a build command was not clean;
- public or hidden assertions failed;
- restored source differs from baseline source;
- two successful clean builds produce different APK identities.

`INCONCLUSIVE` is computed for unavailable infrastructure or missing execution:

- static toolchain validation failed;
- preflight did not pass;
- a build did not run or exited unsuccessfully;
- a correctness suite did not run, executed zero tests or failed at transport
  level without an assertion failure.

`PASS` requires every gate to execute successfully, both independent suites to
run at least one test with zero failures, and restored source/APK identities to
match baseline. The document's caller-provided `status` and `reason_codes` are
accepted only when they exactly equal this recomputation.

## Hidden correctness boundary

CPU-001's private `suite.json` seals source hashes, protected overlay paths and
the exact public correctness command. The hidden source is materialized only in
the evaluator workspace after Agent execution. It must not appear in the public
export, Agent prompt, workspace, logs or artifact search path.

The current hidden Android suite computes all 4096 expected values and their
digest independently from application helpers, then validates the complete
startup state and first-screen readiness. SDK-free validation also rejects
overlay replacement and evaluator/benchmark-detection tokens in application
main source.

## Current limitation

The Schema, deterministic validator, CPU-001 hidden source and private manifest
are implemented and SDK-free tested. No Android dry-run result exists yet: the
hidden source has not compiled, the Gradle dependency locks and APK do not
exist, and no device correctness command has run. The first real result must be
stored under `DEVELOPMENT`; synthetic unit-test fixtures never become evidence.
