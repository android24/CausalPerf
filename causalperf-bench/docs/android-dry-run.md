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

`materialize_hidden_correctness.py` is the only implemented overlay path. It
accepts physically disjoint public/private inputs and a fresh destination,
rejects symlinks and existing destinations, copies rather than mutates the
public task, verifies every hidden file before and after copy, and returns the
suite, input-package and resulting-workspace digests. It runs only in the
evaluator process after the Agent workspace is closed.

The runner-side raw-fact path is now SDK-free executable with fake transports:

- `GradleBuildAdapter` emits the command, source, build result and APK facts;
- `CorrectnessReportParser` derives test/failure/skip counts from JUnit XML and
  emits no caller-selected status;
- `build_android_dry_run.py` normalizes executed/unrun facts, computes the
  outcome and exact artifact registry, seals the document, then validates it.

This separation keeps hidden-suite knowledge and final verdict computation out
of the optimization Agent.

The Agent-side `AndroidDryRunCoordinator` covers only the public execution
slice: clean build, pre-clean, policy-authorized install, public correctness and
post-clean. Its internal DEVELOPMENT summary is not an `AndroidDryRunResult`.
The complete Bench result is created only after the evaluator separately runs
the hidden overlay and restored build. Install and cleanup facts remain linked
through the execution ledger/internal summary; `AndroidDryRunResult` v1 itself
does not add fields that would mutate the released 0.7.0 contract.

`execute_android_dry_run.py` now implements the evaluator closure as an
in-process, dependency-injected coordinator. It refuses to materialize private
inputs unless the public lane, public correctness and post-cleanup all passed;
validates the hidden suite, run, workspace, source and APK bindings; performs
cleanup even when hidden execution raises; and requires a clean RESTORED build.
Only after those operations does it invoke the deterministic result builder.

## Current limitation

The Schema, deterministic validator/builder, CPU-001 hidden source, private
manifest, isolated materializer, structured build facts, correctness parser
and evaluator closure are implemented and SDK-free tested. The public
install/correctness/cleanup orchestration is also implemented behind fake
transports. No real Android dry-run result exists yet:
the hidden source has not compiled, the Gradle dependency locks and APK do not
exist, and no device correctness command has run. The first real result must be
stored under `DEVELOPMENT`; synthetic unit-test fixtures never become evidence.
