# startup-allocation-gc-001

Status: **CANDIDATE — ART/runtime variance risk**

## Objective

Test whether an Agent identifies avoidable startup allocation pressure and
reduces it without removing required object semantics.

## Public scenario

The application constructs an intentionally allocation-heavy intermediate model
during startup and derives a deterministic first-screen result from it.

## Private fault and mechanism

- Injected fault: redundant short-lived objects and oversized intermediates are
  allocated on the startup path.
- Mechanism: allocation CPU cost and, on the reference runtime, GC activity or
  memory pressure delay first frame.
- Expected evidence: elevated allocation-related slices/work, GC when present,
  and reduced allocation/mechanism metrics after intervention.

The causal label is `allocation_pressure`; GC is a possible manifestation, not
guaranteed Ground Truth on every ART version. The task must not require a GC
event if the runtime completes the workload without one.

## Valid intervention classes

- remove redundant intermediate allocations;
- use streaming or compact representations;
- reuse immutable task-local structures safely;
- reduce algorithmic allocation while preserving output.

## Forbidden shortcuts

- call explicit GC as the optimization;
- remove required model entries;
- replace output with benchmark-specific constants;
- increase heap size or weaken memory/correctness checks.

## Correctness assertions

- model output digest and item count match the fixture;
- ordering and duplicate semantics are preserved;
- repeated process launches produce identical visible results.

## Measurement and evidence

- TTID primary; allocation work and total relevant GC pause are mechanism
  metrics.
- Pin ART/API stratum for the frozen task.
- If allocation bytes cannot be measured portably, use versioned trace slices
  and task-owned counters as complementary evidence without treating them as
  production observations.

## Falsification and promotion gate

Reject the hypothesis if the intervention does not reduce allocation mechanism
evidence. Do not freeze the task unless the effect is stable across three
sessions; redesign workload size instead of selectively retrying runs.

