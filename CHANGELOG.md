# Changelog

## Unreleased

### Added

- Phase 1B Android laboratory preflight with pinned host-tool checks, explicit
  device resolution, privacy-preserving device identity, runtime environment
  sampling, schema-valid `EnvironmentSnapshot` output and execution-state
  enforcement before baseline build.
- CPU-001 calibration implementation plan and fail-closed entry/exit gates.
- Verified Gradle 9.5.0 Wrapper files for CPU-001, including locked Wrapper and
  distribution checksums plus POSIX and Windows launchers.
- A CPU-001 toolchain lock and SDK-free validator for AGP, AndroidX, SDK/JDK,
  task identity and clean-build command drift.
- Preflight ordering that does not invoke or download Gradle when required SDK
  components are missing.
- SDK-free development and explicit late binding for Java, ADB, Gradle and the
  Android SDK, allowing later lab activation without source changes.
- Versioned TOML toolchain profiles for macOS, Windows and Linux with native
  executable derivation and deterministic CLI/config/environment/PATH priority.
- `causalperf-contracts@0.7.0` with additive Android dry-run and evaluator-only
  hidden-correctness Schemas; the tagged Phase 1A 0.6.0 contracts remain
  unchanged.
- A deterministic `AndroidDryRunResult` validator that recomputes PASS, FAIL or
  INCONCLUSIVE, requires exact artifact bindings and represents downstream
  `NOT_RUN` steps without fabricated APK or test evidence.
- CPU-001 private Android correctness tests using an independent full-table
  oracle, plus sealed overlay, anti-replacement and anti-detection validation.

## causalperf-contracts@0.6.0 — 2026-08-23

Phase 1A experimental-contract closure.

### Added

- sealed runtime artifacts, canonical digests and a locked 37-Schema bundle;
- deterministic execution, recovery, rollback, causal and statistical gates;
- typed Agent authority, approval, budget and audit enforcement;
- Linux Bubblewrap, macOS sandbox-exec and Windows Sandbox evaluation backends;
- clean public export, private canaries and pre/post leakage scans;
- partition-scoped Task Reproduction Package v2 and lifecycle completeness;
- archived v1 isolation/reproduction Schemas with pure v1→v2 migrations;
- Phase 1A exit audit and Linux/macOS contract CI.

### Validation

- 145 tests pass;
- 37 current and archived Schemas validate and match the bundle lock;
- CPU-001 passes `IMPLEMENTED` and correctly fails `CALIBRATED` readiness;
- four remaining Startup v0.1 tasks remain honest `DRAFT` packages.

### Non-claims

This release contains no Android performance result. CPU-001 has not yet been
built, calibrated or qualified on a device. Linux and Windows isolation still
require scoring-host conformance; CI validates contracts, not host security.
