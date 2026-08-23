# Changelog

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
