# Glossary

This document gives CausalPerf terms one normative meaning.

| Term | Meaning |
|---|---|
| Performance task | One bounded optimization problem, including target metric, source scope, benchmark, correctness tests, environment policy, and budgets. |
| Observation | A measured fact from a trace, benchmark, build, device, or test result. |
| Evidence | A normalized, addressable observation with provenance and validity metadata. |
| Evidence bundle | Immutable collection of evidence used by one diagnosis or experiment. |
| Hypothesis | A proposed causal mechanism that explains the target regression. |
| Prediction | A preregistered, measurable consequence expected if an intervention changes the hypothesized mechanism. |
| Falsification condition | A result that rejects or materially weakens a hypothesis. |
| Intervention | One reviewable and reversible change intended to alter a causal mechanism. |
| Baseline | Measurements from the unmodified target revision under the declared environment. |
| Treatment | Measurements after one accepted intervention under the same declared environment. |
| Replication | A fresh execution intended to confirm that an observed effect is reproducible. |
| Reversal | Reapplying the baseline state after treatment to test whether the effect reverses. |
| Practical threshold | Minimum effect large enough to matter for the task, independent of statistical uncertainty. |
| Statistical verdict | PASS, FAIL, FLAKY, or INCONCLUSIVE based on the task measurement policy. |
| Correctness gate | Protected test that verifies intended application behavior remains intact. |
| Integrity gate | Check that the agent did not alter protected assets, remove required work, or exploit the benchmark. |
| Environmental validity | Whether device, compilation, thermal, battery, and background-load conditions satisfy policy. |
| Ground Truth | Private evaluator knowledge describing injected fault, mechanism, expected evidence, valid intervention classes, and reference fix. |
| Causally supported | A hypothesis whose preregistered prediction survived correctness, integrity, environmental, mechanism, performance, and replication checks. |
| Inconclusive | Evidence is insufficient or unstable; not equivalent to no defect. |
| Experiment ledger | Append-only record of inputs, approvals, commands, artifacts, state transitions, measurements, and decisions. |

## Two independent state domains

An experiment execution state describes what the system is doing. A hypothesis
epistemic state describes what is known. They must never share one enum.

```text
Execution:  CREATED -> VALIDATING -> ... -> COMPLETED
Knowledge:  PROPOSED -> EVIDENCE_SUPPORTED -> ... -> CAUSALLY_SUPPORTED
```

