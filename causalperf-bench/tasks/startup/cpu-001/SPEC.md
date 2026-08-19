# startup-main-thread-cpu-001

Status: **IMPLEMENTED DRAFT — build and pilot calibration pending**

The public Android task, Macrobenchmark, correctness test, private Ground Truth,
and expert patch are present. This status does not imply that the project has
compiled or that its injected effect is reproducible on a physical device.

## Objective

Test whether an Agent can identify and remove nonessential CPU computation from
the cold-start critical path without changing the first screen's required data.

## Public scenario

The application deterministically derives a presentation-independent lookup
table during `Application.onCreate`. The first activity displays a fixed set of
values whose correctness is tested. Startup is intentionally slower than the
expert baseline.

## Private fault and mechanism

- Injected fault: recompute an invariant lookup table on the main thread for
  every process start.
- Mechanism: sustained main-thread running time delays lifecycle work and first
  frame.
- Expected evidence: high main-thread CPU/running contribution inside startup,
  low runnable starvation, and no dominant Binder or I/O contribution.

## Valid intervention classes

- precompute the invariant at build time;
- cache a verified static representation;
- move non-first-frame computation after initial display if behavior permits;
- reduce algorithmic complexity while preserving the exact output.

## Forbidden shortcuts

- remove or weaken the displayed-result correctness assertion;
- skip required computation only when a benchmark process is detected;
- hard-code the benchmark assertion without preserving the specified behavior;
- modify benchmark iterations or target metric.

## Correctness assertions

- first-screen value digest matches the task fixture;
- cold relaunch produces the same digest;
- no placeholder remains after the declared readiness point;
- benchmark and release variants execute the same application path.

## Measurement and evidence

- TTID primary; main-thread CPU time is the mechanism metric.
- Pilot target: injected regression and reference improvement must each exceed
  `max(50 ms, 10%)` with the task measurement policy.
- Reference device and workload size are selected after variance pilot.

## Falsification

Reject the CPU hypothesis if reducing the targeted computation does not reduce
main-thread CPU evidence or does not improve TTID under valid repeated runs.
