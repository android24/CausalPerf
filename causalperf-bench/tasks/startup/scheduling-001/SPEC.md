# startup-scheduling-starvation-001

Status: **CANDIDATE — high reproducibility risk**

## Objective

Test whether an Agent distinguishes CPU work from time spent runnable but not
scheduled during cold startup.

## Public scenario

A task-owned contention workload competes with the application main thread on a
pinned reference execution environment. The startup computation itself is
small, but the main thread experiences measurable runnable delay.

## Private fault and mechanism

- Injected fault: deterministic CPU contention begins before launch and ends
  after first frame.
- Mechanism: the main thread is runnable but receives insufficient CPU time.
- Expected evidence: runnable latency increases while main-thread CPU work stays
  materially below total startup delay.

## Environment constraint

This task must run in a dedicated emulator or controlled physical-device profile
whose CPU topology and contention mechanism are pinned. If non-root controls
cannot reproduce the effect across three independent sessions, the task is
redesigned or removed from v0.1.

## Valid intervention classes

- reduce or reschedule task-owned competing work;
- correct inappropriate priority or concurrency configuration;
- move noncritical contention outside the startup interval.

## Forbidden shortcuts

- change emulator/device CPU count;
- disable benchmark environmental checks;
- increase startup timeout instead of reducing contention;
- remove required background work rather than reschedule it.

## Correctness assertions

- required competing workload completes with the expected digest;
- startup screen result remains correct;
- concurrency/lifecycle behavior remains valid after process recreation.

## Measurement and evidence

- TTID primary; main-thread runnable duration and scheduling latency are
  mechanism metrics.
- Record CPU topology, online cores, contention process/thread state, thermal
  state, and background-load fingerprint for every block.

## Falsification and promotion gate

Reject the scheduling hypothesis if intervention changes main-thread CPU work
rather than runnable latency. Do not freeze this task until injected regression,
reference improvement, and baseline stability pass three-session calibration.

