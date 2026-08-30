# Phase 1B — CPU-001 Calibration Pilot

## Purpose

Phase 1B determines whether CPU-001 can become a reproducible benchmark. It
does not evaluate CausalPerf Agent and cannot produce a publishable performance
claim. Every measurement, trace, threshold change and derived report created in
this phase belongs permanently to the `CALIBRATION` partition.

The Phase 1A contracts remain the authority boundary. Android integration may
expose a missing field or an unusable threshold, but any contract change must
be versioned and forces fresh calibration data. Calibration observations may
never be copied into `QUALIFICATION` or `EVALUATION`.

Android tooling is deliberately late-bound. SDK-free development remains the
default lane, governed by the
[Android SDK late-binding contract](android-sdk-late-binding.md).

## Ordered implementation slices

### 1B.1 Android laboratory preflight — IMPLEMENTED

The trusted runner now has a read-only `AndroidEnvironmentCollector`. It:

- requires an explicit device serial and never auto-selects a target;
- requires Java 17+, ADB, Gradle 9.5.0, Android Platform 36 and Build Tools
  36.0.0;
- resolves one online physical Android 14–16 arm64 device;
- records hashed device and build identities rather than the raw serial or
  fingerprint;
- samples battery, charging, thermal state, online CPUs, available memory and
  background CPU load;
- emits a content-addressed `EnvironmentSnapshot` on a complete device probe;
- returns `INCONCLUSIVE` before build, install or measurement when a host,
  transport or environment gate is not satisfied.

`PreflightExecutionAdapter` binds this probe to the controller's
`PREPARING_ENVIRONMENT` transition. A passing snapshot digest enters the
experiment ledger before baseline build; an inconclusive result prevents the
delegate adapter from reaching `BUILDING_BASELINE`.

Run the probe from the repository root:

```bash
PYTHONPATH=causalperf-agent/src:shared/reference \
python3.12 -m causalperf_agent.android.preflight \
  --device-serial <explicit-serial> \
  --environment-id ENV-CPU-001-PREFLIGHT
```

Java, ADB, Gradle and SDK locations may also be supplied with the explicit
`--java-executable`, `--adb-executable`, `--gradle-executable` and `--sdk-root`
arguments, or with a host-specific TOML profile. No global environment change
is required.

This command is inspection-only. It does not install an APK, change compilation
state, start an application or create calibration evidence.

### 1B.2 Reproducible task build and correctness — PARTIALLY IMPLEMENTED

Completed without requiring an Android SDK:

1. a verified Gradle 9.5.0 Wrapper is checked in for POSIX and Windows hosts;
2. the Wrapper JAR, scripts, distribution URL and official distribution
   checksum are bound by `toolchain.lock.json`;
3. AGP 9.3.0, AndroidX Benchmark 1.4.1 and AndroidX Test declarations are
   centralized and pinned in the version catalog;
4. Java 17, Platform/target SDK 36 and Build Tools 36.0.0 requirements are
   machine-validated against the project and task manifest;
5. every Android command uses the task-local Wrapper and the baseline build
   begins with `clean`;
6. SDK-free tests reject Wrapper tampering, checksum changes, dependency-version
   drift, task identity drift and removal of the clean-build step.
7. an evaluator-only CPU-001 hidden suite independently validates all 4096
   values, the full table digest and first-screen readiness; its sealed overlay
   manifest and anti-detection checks are validated outside the public package;
8. the Bench-owned `AndroidDryRunResult` v1 contract represents executed and
   unrun steps, exact artifact bindings and a deterministic computed outcome.
9. a private evaluator materializer validates both packages, rejects symlinks,
   copies the public task into a fresh physically disjoint workspace, overlays
   only digest-sealed hidden files, and verifies that neither input changed;
10. a structured Gradle build adapter and JUnit correctness parser are tested
    with injected fake transports, and a Bench-owned builder composes their raw
    facts into a validated `AndroidDryRunResult` without accepting a caller
    selected PASS/FAIL status.

Still required on the Android laboratory lane:

1. install or bind JDK 17, Android SDK Platform 36 and Build Tools 36.0.0;
2. resolve dependencies once and generate Gradle dependency lock state outside
   the scoring sandbox;
3. build `:app:assembleBenchmark` from a clean workspace and record command,
   source-tree, toolchain and APK digests;
4. use the implemented materializer to create the sealed hidden overlay in an
   isolated evaluator workspace, compile it, then run public and hidden checks
   on the explicitly resolved device;
5. prove that baseline restoration reproduces the original source and APK
   identities and emit the first real `AndroidDryRunResult`.

The checked-in version declarations are a toolchain lock, not evidence that
AGP 9.3.0 and Gradle 9.5.0 have successfully resolved together. Only the first
real clean build can establish that compatibility and produce dependency locks.

No timing run begins until clean build and correctness pass.
The exact pre-calibration evidence and failure semantics are defined in the
[Android task dry-run contract](../causalperf-bench/docs/android-dry-run.md).

### 1B.3 Guarded Android execution adapters — SDK-FREE IMPLEMENTED; LAB VALIDATION PENDING

Implemented in the SDK-free lane:

- an exact-argv `ProcessTransport` with no shell, explicit environment,
  timeout and bounded returned output;
- `GradleBuildAdapter` for clean baseline/treatment builds, immutable request
  configuration, source mutation detection and content-addressed build/APK
  outputs;
- `GradleBuildExecutionAdapter` at the frozen build states, with tested
  `GuardedExecutionAdapter` authorization and denial behavior;
- POSIX and Windows task-relative Wrapper requests;
- a trusted raw JUnit parser that rejects DTD/entity declarations, malformed
  XML, zero-test false positives, assertion failures and incomplete process
  outcomes;
- public/hidden correctness normalization and deterministic dry-run result
  composition owned by Bench rather than the Agent.
- a policy-scoped ADB install adapter that verifies APK bytes, selects one
  hashed device, verifies the installed package and never exposes the raw
  serial in its durable result;
- an idempotent trusted cleanup adapter that distinguishes an absent package
  from an unavailable device, uninstalls only an explicit package allowlist and
  verifies absence;
- a Gradle correctness Runner that binds `ANDROID_SERIAL`, removes stale JUnit
  output, bounds XML collection, and rejects source/APK drift;
- a public-task dry-run coordinator covering build, pre-clean, authorized
  install, public correctness and post-clean, including cleanup-on-failure.
- a policy-authorized Macrobenchmark adapter for A1/B/A2 that keeps raw device
  serials out of durable requests, rejects source/APK drift, clears stale
  outputs and seals bounded AndroidX JSON and Perfetto trace files;
- a deterministic normalizer that requires the exact benchmark, metric,
  iteration count and one indexed trace per measurement before emitting a
  schema-valid `MeasurementSet`;
- an evaluator-only coordinator that materializes the sealed hidden suite,
  executes hidden correctness, always cleans evaluator-owned packages, performs
  a clean restored build and delegates the final verdict to Bench.

Still required on the Android lane is real-device validation of every adapter
and the first real evaluator execution. Correctness execution and cleanup
remain trusted Runner gates rather than new Agent capabilities: the frozen
Phase 1A tool contract has not been silently expanded to expose private
evaluator tests or arbitrary uninstall operations to the Agent.

All commands must remain bound to the policy's working directory, package and
hashed device identity. Transport failures must preserve the existing retry
and rollback semantics.

The Agent-side public dry run stops after baseline build, install, public
correctness and cleanup. Only after that process is closed may the evaluator
materialize hidden correctness and perform the restored build. Intervention and
private Ground Truth access are not part of either dry-run lane.

### 1B.4 A1/B/A2 calibration executor — PENDING

After 1B.2 and 1B.3 pass:

1. open a new `CALIBRATION` session and seal its environment and statistical
   policies;
2. collect three stabilization launches followed by 30 included cold-start
   measurements for A1;
3. register the mechanism prediction before applying the reference patch;
4. rebuild, run correctness and collect B;
5. restore the exact baseline, rebuild, run correctness and collect A2;
6. retain every invalid sample with a preregistered exclusion code;
7. retain Macrobenchmark JSON, Perfetto traces and pre/post environment
   snapshots with source and APK bindings;
8. roll back and return `INCONCLUSIVE` on environment, integrity, correctness
   or baseline-drift failure.

The per-block measurement transport and artifact normalizer required by this
executor are now implemented. Session orchestration, the three-launch
stabilization runner, reference intervention/rollback, inter-block environment
snapshots and restart-safe checkpoint composition remain pending.

At least three independent sessions are required. Extra retries cannot be added
after observing an unfavorable effect.

### 1B.5 Calibration decision and protocol freeze — PENDING

The pilot must report within-session variance, between-session variance,
baseline drift, invalid-sample rate, TTID effect, main-thread CPU mechanism
effect and uncertainty. The result is one of:

- `CALIBRATABLE`: choose and preregister the Phase 1C sample count and limits;
- `REDESIGN_REQUIRED`: revise the task or measurement protocol and start a new
  calibration version;
- `UNSTABLE`: remove CPU-001 from qualification until the cause is controlled.

Only a new, frozen Phase 1C task version may collect qualification data.

## Current laboratory status

The macOS development host inspected on 2026-08-24, 2026-08-25 and 2026-08-27 is not ready
for Android calibration. Its default Java is 8, while Android Studio contains
JBR 17.0.11 and SDK Platform Tools contain ADB 35.0.1 at explicit paths that are
not configured in the shell. The SDK currently stops at Platform 35 and Build
Tools 35.0.0; Platform 36 and Build Tools 36.0.0 are absent. The verified
Gradle 9.5.0 Wrapper is now present in the task, but its distribution has not
been downloaded or executed because the SDK gate already fails. No Android
device is connected. These are explicit infrastructure blockers, not benchmark
failures. No APK, Android correctness result, measurement or trace has been
recorded.

## Phase 1B exit criteria

Phase 1B is complete only when all of the following are true:

- the preflight passes on a named reference-device configuration;
- CPU-001 builds from a clean checkout using pinned tools;
- public and hidden correctness tests pass before and after intervention;
- at least three independent A1/B/A2 calibration sessions validate;
- all raw artifacts are bound to `CALIBRATION` and pass lifecycle validation;
- variance, sample count, drift, environment and mechanism limits are chosen
  from the pilot and frozen in a new protocol/task version;
- calibration artifacts are sealed against reuse in Phase 1C.

Passing these criteria authorizes Phase 1C data collection; it does not itself
qualify CPU-001.
