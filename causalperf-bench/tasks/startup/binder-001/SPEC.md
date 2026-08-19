# startup-binder-blocking-001

Status: **DRAFT — requires transaction-correlation validation**

## Objective

Test whether an Agent can trace a synchronous client call across a process
boundary and remove it from the first-frame critical path safely.

## Public scenario

The benchmark app binds to a task-owned service running in a separate process.
Startup performs a synchronous request on the main thread. The response is
deterministic and is not required to render the initial screen.

## Private fault and mechanism

- Injected fault: a synchronous main-thread Binder call waits for deterministic
  server-side work before first frame.
- Mechanism: client main thread blocks until the remote service replies.
- Expected evidence: client transaction, target process/thread, server work,
  reply, and overlapping client wait inside the startup interval.

Using a task-owned process avoids dependence on unstable `system_server`
behavior and gives the evaluator a known causal mechanism.

## Valid intervention classes

- defer the request until after first frame;
- use asynchronous IPC with correct lifecycle handling;
- cache a semantically valid response;
- remove a demonstrably redundant transaction.

## Forbidden shortcuts

- remove the service feature entirely;
- return a benchmark-only fake response;
- change server delay or benchmark configuration;
- hide Binder trace instrumentation.

## Correctness assertions

- initial screen renders the required independent content;
- remote result eventually arrives and matches the fixture;
- process death/rebind path works;
- lifecycle cancellation does not leak or update a destroyed activity.

## Measurement and evidence

- TTID primary; main-thread Binder wait and server duration are mechanism
  metrics.
- Trace configuration must include Binder and scheduler data.
- Transaction correlation query and expected trace schema are versioned with the
  task.

## Falsification

Reject the Binder hypothesis if removing the synchronous dependency does not
reduce client wait evidence or does not improve TTID in valid repeated runs.

