# startup-main-thread-io-001

Status: **DRAFT — requires cache-control pilot**

## Objective

Test whether an Agent can identify synchronous file I/O on the startup main
thread and choose an optimization that preserves required data semantics.

## Public scenario

During cold start, the application synchronously reads and parses a deterministic
local data file before drawing the first activity. The first screen exposes a
digest and selected records verified by correctness tests.

## Private fault and mechanism

- Injected fault: repeated blocking read/parse work executes on the main thread.
- Mechanism: the startup critical path waits for local I/O and parsing.
- Expected evidence: main-thread I/O slices or syscalls overlap startup and
  blocked/running time changes with the intervention.

## Valid intervention classes

- persist a validated compact/indexed representation;
- defer data not required for first frame;
- move work off-main while preserving readiness semantics;
- eliminate redundant reads without returning stale or incomplete data.

## Cache-control design

Page cache makes I/O benchmarks fragile. The task must declare whether it tests
cold storage, warm local cache, or redundant synchronous access. Initial v0.1
should prefer redundant parsing/read work that remains measurable without root
cache-dropping commands. Root-only global cache manipulation is outside scope.

## Forbidden shortcuts

- remove required records or display placeholders past the readiness point;
- replace data with a benchmark-specific constant;
- disable the correctness digest;
- read from network or undeclared external storage.

## Correctness assertions

- parsed data digest and record count match the fixture;
- first-screen required records are present at readiness;
- repeated launch and process death preserve semantics;
- invalid/corrupt fixture behavior remains covered.

## Measurement and evidence

- TTID primary; main-thread I/O/parse duration is the mechanism metric.
- Capture file digest, installation state, application data state, and cache
  policy in the environment manifest.

## Falsification

Reject the I/O hypothesis if the intervention leaves main-thread I/O evidence
unchanged or improves TTID only by postponing work required before readiness.

