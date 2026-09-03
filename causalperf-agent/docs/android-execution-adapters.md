# Android execution adapters

## Boundary

The Android adapter layer is trusted runner code. The model may propose a typed
operation, but cannot supply a shell program, declare its own success, access a
private correctness suite, or bypass `RuntimePolicy`.

```text
typed request
    -> RuntimePolicy authorization and budget reservation
    -> exact process transport
    -> raw output and filesystem facts
    -> sealed shared artifact
    -> Bench-owned gate computation
```

The current slice is deliberately transport-injectable. Unit tests can execute
the entire evidence path with no JDK, Android SDK, Wrapper download, emulator or
device. A later-bound laboratory transport uses the same requests and outputs.

## Gradle build request

`GradleBuildRequest` binds one run and role to:

- a task root and task-relative `gradlew` or `gradlew.bat`;
- an argument vector whose first item is `clean`;
- an explicit child environment, timeout and returned-output limit;
- one source root and one expected APK path contained by the task root;
- source artifact and pinned toolchain identities;
- the DEVELOPMENT, CALIBRATION, QUALIFICATION or EVALUATION partition.

The adapter hashes the source before process execution and again afterwards.
Path traversal, symlinked inputs, source mutation, timeout, truncated output,
non-zero exit and missing APK are distinct fail-closed outcomes. Only a zero
exit with an unchanged source and existing expected APK emits an APK artifact.
The command submitted to policy and the command executed by the transport are
derived from the same immutable request.

Build authorization is implemented for the controller's baseline and treatment
build states. Restored-build orchestration for the pre-calibration dry run is a
Bench runner concern and does not change the A1/B/A2 state contract.

## Correctness facts

`CorrectnessReportParser` accepts already captured JUnit XML plus sealed suite,
source, APK and command identities. It counts testcase elements rather than
trusting XML aggregate attributes, treats both `failure` and `error` as
failures, and rejects a testcase that is simultaneously failed and skipped.
DOCTYPE/ENTITY declarations and malformed roots are not parsed.

The shared `CorrectnessReport` deliberately has no `status` field. A trusted
computed gate returns:

- `FAIL` when an assertion or test error is present;
- `INCONCLUSIVE` for malformed/unsafe XML, zero tests, transport timeout,
  returned-output truncation, or non-zero process exit without an assertion;
- `PASS` only for at least one parsed testcase, zero failures, a zero process
  exit and a complete process outcome.

Public and hidden reports use different suite IDs and suite digests. The hidden
suite is materialized and executed by the evaluator after Agent execution; it
is not added as an Agent tool capability.

`GradleCorrectnessRunner` now executes the sealed task-local Wrapper command in
the selected workspace. `ANDROID_SERIAL` must equal the explicitly resolved
device, the source and APK must match their pre-run digests, and stale JUnit
output is removed before execution. Only bounded, regular XML files beneath a
declared build-output directory are collected. The source and APK are checked
again after execution, so a correctness command cannot silently rebuild a
different artifact and still pass the gate.

## ADB install and cleanup

`AdbInstallRequest` binds the raw local serial to the hashed device identity,
but only the hash enters the `install_apk` ToolRequest and result record. The
adapter verifies the APK bytes before invoking the fixed exact-argv sequence:

```text
adb -s <explicit serial> install -r --no-streaming <verified APK>
adb -s <explicit serial> shell pm path <package>
```

A zero install exit without a verifiable installed package is inconclusive.
Timeout, output truncation, APK drift and policy denial cannot become PASS.
`AdbInstallExecutionAdapter` prepares the exact request before
`GuardedExecutionAdapter` authorization; denied package/device identities never
reach the transport.

Cleanup is trusted Runner behavior rather than an Agent capability. It operates
only on an explicit sealed package list and device, force-stops installed
packages, uninstalls them and verifies absence. An already absent package is an
idempotent PASS only when `pm path` itself completes successfully with no
package result; an offline device is not mistaken for successful cleanup.

## Public dry-run coordinator

`AndroidDryRunCoordinator` implements the SDK-free-testable public lane:

```text
clean baseline build
    -> pre-clean owned packages
    -> policy-authorized APK install
    -> public correctness
    -> post-clean owned packages
```

Every request is checked against the runtime build's run, workspace, source,
APK and device identities. Build or pre-clean failure stops downstream work;
install failure skips correctness but still triggers post-cleanup; cleanup
failure vetoes an otherwise passing run. The coordinator emits a sealed
DEVELOPMENT summary with `scope: PUBLIC_TASK_ONLY`.

This summary is an Agent-internal execution record, not the Bench-owned
`AndroidDryRunResult`. The evaluator later materializes and runs hidden
correctness, performs the restored clean build, and asks the deterministic
Bench builder to compute the complete result. This preserves the private-data
boundary.

## Macrobenchmark and Perfetto outputs

`GradleBenchmarkRunner` implements the frozen `run_benchmark` capability for
the A1, B and A2 measurement states. It removes the declared stale Gradle
output root, verifies source and APK bytes before and after execution, runs the
task-local Wrapper with exact argv, and collects only bounded regular JSON and
Perfetto trace files. Every AndroidX JSON file and every per-iteration trace is
sealed as a raw-byte `Artifact`; calibration artifacts receive permanent
retention.

The normalizer selects exactly one declared benchmark and metric, requires the
AndroidX `repeatIterations` and `metrics.<metric>.runs` values to agree, and
requires one uniquely indexed trace for every included iteration. Only then is
a schema-valid `MeasurementSet` produced. A specifically indexed missing trace
is retained as an excluded sample with the preregistered
`MISSING_REQUIRED_ARTIFACT` code. Duplicate/unindexed traces,
malformed/non-finite values, unexpected iteration count, output limits, timeout,
process failure and input drift remain fail-closed.

The raw device serial is absent from the durable `run_benchmark` ToolRequest.
Policy authorizes the hashed device identity; the trusted Runner injects the
matching raw `ANDROID_SERIAL` only into the transport environment. A non-zero
warmup count also requires pre-existing stabilization evidence, preventing the
three required stabilization launches from being asserted only as metadata.

## Evaluator dry-run closure

After the public coordinator has passed and post-cleaned, Bench's
`execute_android_dry_run.py` materializes the sealed hidden overlay in a fresh
physically disjoint workspace, executes hidden correctness through an injected
trusted Runner, always performs evaluator cleanup, and runs a clean RESTORED
build. It validates hidden suite ID/digest and source/APK/workspace/run bindings
before asking the deterministic Bench builder to create the complete
`AndroidDryRunResult`. Cleanup failure prevents creation of a passing result,
and hidden exceptions still trigger cleanup.

## Calibration block and session execution

`StartupStabilizationRunner` issues exactly three explicit ADB cold launches
before each measured block. It checks that the package is installed, performs
force-stop, `am start -W`, Home and final force-stop operations with exact argv,
requires observable launch completion, does not auto-retry, and verifies that
source and APK bytes did not change. The raw serial remains transport-only.

`CalibrationMeasurementExecutionAdapter` composes a fresh environment probe,
stabilization evidence and the already authorized Macrobenchmark request for
each of `MEASURING_A1`, `MEASURING_B` and `MEASURING_A2`. The actual command,
device, package, partition and sequence must reproduce the pre-execution
ToolRequest digest. Passing blocks are atomically persisted by
`CalibrationBlockStore`; nested environment, stabilization, benchmark,
MeasurementSet and trace registries are cross-checked again on load.

`CalibrationProtocolExecutionAdapter` records only environment/statistical/
sequence commitments at session open. Prediction and intervention digests enter
the Ledger after A1 and must be present before the intervention state. Session
verification recomputes order, included/invalid counts, raw environment-policy
compliance, cross-arm ID/trace uniqueness, treatment source change, and exact
A1/A2 source/APK restoration. It produces a CALIBRATION verification summary,
not a causal or calibratability decision.

## Host configuration

SDK/JDK/Gradle paths stay late-bound through the host-specific toolchain TOML
profile. POSIX hosts select `gradlew`; Windows selects `gradlew.bat`. Requests
carry an explicit environment and policy must allow exactly those environment
keys. Local paths and credentials are never written into task manifests or
portable evidence.

## Remaining implementation

The SDK-free implementation now covers build, install, public/hidden
correctness, cleanup, restored-build composition, Macrobenchmark result
collection and one-to-one Perfetto trace binding. It has not yet been exercised
against a real connected Android device and therefore has not produced a real
APK, JUnit result, `MeasurementSet`, trace artifact or `AndroidDryRunResult`.
The remaining work is laboratory validation, concrete reference-patch and
restored build/install/correctness wiring, mechanism queries and the three real
sessions required before the Phase 1B.5 decision.
