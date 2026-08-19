# CausalPerf Shared

This directory contains versioned semantics consumed by both CausalPerf Agent
and CausalPerf Bench:

- `docs/`: normative causal, statistical, measurement, execution and schema
  protocols;
- `schemas/`: cross-project JSON artifact contracts;
- `reference/`: deterministic reference validation and tests.

Android orchestration belongs in `causalperf-agent`; task construction, private
Ground Truth, leakage enforcement and scoring belong in `causalperf-bench`.
