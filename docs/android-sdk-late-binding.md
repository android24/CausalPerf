# Android SDK Late-Binding Contract

## Decision

Android SDK, Gradle, ADB and a physical device are optional for normal
CausalPerf development and mandatory only for explicitly requested Android lab
runs. Their absence must never prevent contract, policy, statistical,
isolation, parser or state-machine development.

Installing the missing laboratory components later must activate Android
execution through configuration only. It must not require changes to Agent,
Bench or shared-contract source code.

## Two execution lanes

### SDK-free development lane

This is the default local and CI lane:

```bash
sh tools/test_all.sh
```

It uses fake command transports and synthetic Android observations. It validates
argument vectors, failure semantics, schemas, digests, state transitions,
policy boundaries, rollback and lifecycle rules without importing an Android
SDK library or starting ADB.

The lane must remain valid on a machine with none of the following:

- Android Studio or Android SDK;
- Java or Gradle;
- ADB or an attached device;
- Perfetto binaries;
- Android environment variables.

Synthetic observations are test fixtures, never benchmark measurements.

### Android laboratory lane

This lane is opt-in. It begins with the read-only preflight and cannot reach a
build or measurement state until the environment passes. Tools may be resolved
from the process environment or supplied explicitly:

```bash
PYTHONPATH=causalperf-agent/src:shared/reference \
python3.12 -m causalperf_agent.android.preflight \
  --device-serial <explicit-physical-device-serial> \
  --environment-id ENV-CPU-001-PREFLIGHT \
  --java-executable <jdk-17-or-newer>/bin/java \
  --adb-executable <android-sdk>/platform-tools/adb \
  --gradle-executable causalperf-bench/tasks/startup/cpu-001/public-task/gradlew \
  --sdk-root <android-sdk>
```

Explicit paths take precedence over ambient discovery. This means an SDK may be
kept on an external volume or added months later without changing repository
code or global shell configuration.

## Cross-platform configuration

The versioned TOML format supports separate macOS, Windows and Linux profiles.
Start from
[`config/android-toolchains.example.toml`](../config/android-toolchains.example.toml)
and save local values as `config/android-toolchains.local.toml`. The local file
is ignored by Git because absolute paths may contain workstation-specific
information.

```bash
PYTHONPATH=causalperf-agent/src:shared/reference \
python3.12 -m causalperf_agent.android.preflight \
  --toolchain-config config/android-toolchains.local.toml \
  --device-serial <explicit-physical-device-serial>
```

On Windows, use Python 3.12 and separate `PYTHONPATH` entries with `;`. TOML
literal strings preserve paths such as `D:\Android\Sdk` without double
escaping. The selected profile must declare the actual host OS; a Windows
profile is rejected on Linux or macOS and vice versa.

Configuration precedence is deterministic:

1. individual CLI arguments such as `--jdk-home`, `--sdk-root`,
   `--gradle-home` or `--adb-executable`;
2. the selected TOML profile;
3. `JAVA_HOME`, `ANDROID_SDK_ROOT`/`ANDROID_HOME` and `GRADLE_HOME`;
4. native `PATH` discovery for any tool still unresolved.

CLI root overrides derive native executable names: `java.exe`, `adb.exe` and
`gradle.bat` on Windows, and `java`, `adb` and `gradle` on macOS/Linux. A
provided executable path is exact: if it does not exist, the preflight fails
instead of silently selecting another version from `PATH`.

## Required architectural properties

Every Android-facing implementation must preserve these properties:

1. no tool discovery, subprocess, device connection or filesystem mutation at
   module-import time;
2. exact argument-vector execution without a shell;
3. injectable command transport, clock, filesystem probes and device
   observations for SDK-free tests;
4. explicit device selection; never select the first attached device;
5. fail-closed `INCONCLUSIVE` results for missing tools, devices or environment
   facts;
6. no automatic SDK download, license acceptance or system-wide configuration;
7. real artifacts are accepted only after the laboratory preflight and remain
   bound to their source, toolchain, device and partition digests;
8. SDK-free CI must stay green independently of optional host-conformance jobs.

The preflight validates the SDK filesystem before executing any resolved host
tool. This ordering is intentional: a missing Platform or Build Tools package
returns `INCONCLUSIVE` without starting the Gradle Wrapper and therefore
without downloading the pinned Gradle distribution.

An adapter that can be tested only with a local Android installation does not
meet this contract.

## Later activation checklist

When storage and a physical device become available:

1. install the task-pinned Platform and Build Tools on any chosen volume;
2. provide JDK, ADB, Gradle Wrapper and SDK paths explicitly or through a
   dedicated lab environment;
3. connect one Android 14–16 arm64 physical device and record its intended lab
   role;
4. run Preflight and resolve every reason code;
5. run the baseline build/correctness dry run;
6. enable `CALIBRATION` collection only after that dry run passes.

No previously generated synthetic fixture becomes Android evidence after SDK
installation. The first real run starts a new calibration session.

## What “immediately usable” means

After the pinned components and device are available, the existing code can
probe and use them without recompilation or source edits. Actual calibration is
still conditional on Preflight, clean build, dependency availability and
correctness passing. Late binding removes the development dependency; it does
not bypass experimental validity gates.
