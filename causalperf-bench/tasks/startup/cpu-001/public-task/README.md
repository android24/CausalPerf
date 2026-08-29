# startup-main-thread-cpu-001 public task

This package contains only information available to an evaluated Agent. Private
Ground Truth, hidden evaluator checks, and the expert patch are packaged from
the sibling `private-evaluator` directory and must not be mounted into the Agent
sandbox.

## Prerequisites

- JDK 17
- Android SDK Platform 36 and Build Tools 36.0.0
- The checked-in, SHA-256-pinned Gradle 9.5.0 Wrapper
- One explicit Android 14+ physical-device serial for performance runs

## Commands

```bash
./gradlew clean :app:assembleBenchmark
./gradlew :app:connectedBenchmarkAndroidTest
./gradlew :macrobenchmark:connectedCheck
```

On Windows use `gradlew.bat` with the same arguments. The Wrapper JAR, scripts,
distribution URL and distribution checksum are pinned by
`toolchain.lock.json`; local SDK/JDK paths remain machine configuration and are
never stored in this public task.

Macrobenchmark is configured for 30 cold-start iterations with
`CompilationMode.None`. Results and Perfetto traces are produced by the AndroidX
Benchmark runner under the module's connected-test output directory.

## Current implementation status

Source, public tests, the Wrapper and static toolchain validation are present.
The task has not been built or run in this repository environment: the host has
an explicitly addressable JBR 17 and ADB, but Platform 36, Build Tools 36.0.0
and a connected physical device are unavailable. The Gradle distribution has
not been downloaded because the SDK preflight fails first. CPU-001 remains
`IMPLEMENTED`, not `CALIBRATED`, until the Android dry run, evaluator-only
hidden tests and pilot calibration complete.
