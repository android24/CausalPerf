# startup-main-thread-cpu-001 public task

This package contains only information available to an evaluated Agent. Private
Ground Truth, hidden evaluator checks, and the expert patch are packaged from
the sibling `private-evaluator` directory and must not be mounted into the Agent
sandbox.

## Prerequisites

- JDK 17
- Android SDK Platform 36 and Build Tools 36.0.0
- Gradle 9.5.0 (temporary until a verified Gradle Wrapper is checked in)
- One explicit Android 14+ physical-device serial for performance runs

## Commands

```bash
gradle :app:assembleBenchmark
gradle :app:connectedBenchmarkAndroidTest
gradle :macrobenchmark:connectedCheck
```

Macrobenchmark is configured for 30 cold-start iterations with
`CompilationMode.None`. Results and Perfetto traces are produced by the AndroidX
Benchmark runner under the module's connected-test output directory.

## Current implementation status

Source and test scaffolding are present. The task has not been built or run in
this repository environment because the required JDK, Android SDK, Gradle, and
ADB toolchain is not installed. It remains a draft until pilot calibration and
private evaluator validation complete.

