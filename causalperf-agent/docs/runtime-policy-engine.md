# Runtime Policy Engine

The Phase 1A Policy Engine is the model-independent authorization boundary for
Agent tool requests. It decides whether a typed request may be dispatched; it
does not infer permission from an Agent explanation or from repository text.

## Inputs and outputs

```text
sealed RuntimePolicy + immutable ToolRequest + ExecutionSnapshot
    -> ALLOW | DENY | REQUIRE_APPROVAL
    -> budget reservation and rollback obligation
    -> contract-valid ToolCall audit record
```

The policy is bound to one run, hashed device identity, application package,
partition set, path set, executable set, environment-key set and immutable
resource budget. Its content digest is copied into the execution snapshot on
the first allowed call. A later policy digest mismatch fails closed.

## Decision order

Authorization runs before the controller records an execution `INTENT` and
before the trusted adapter performs a side effect:

1. reject unknown tools and run/policy mismatches;
2. validate path, command, device, package and partition scope;
3. require the applicable task or per-action approval;
4. compare requested resources with the remaining immutable budget;
5. atomically reserve budget and register any rollback obligation;
6. persist the snapshot and authorization ledger event;
7. dispatch through the guarded adapter.

A denial never invokes the delegate adapter. A request awaiting approval ends
the current controller run as `INCONCLUSIVE`; resumption requires a new request
bound to an existing approval record.

## Command and filesystem rules

- Requests use an executable and argument array, not a shell program.
- Shell executables are rejected even if accidentally included in an
  executable allowlist.
- Control operators, command substitution, newlines, NUL bytes and unresolved
  globs are rejected.
- Working directories and environment keys are allowlisted.
- Relative paths containing `..` and paths outside declared writable roots are
  rejected.
- Benchmark, correctness, evaluator and policy roots belong in
  `protected_paths`; writable and protected roots may not overlap.

These checks are semantic defenses in the Runner. OS process, mount and network
isolation are separate WP6 controls and remain mandatory for evaluation.

## Approval semantics

R0 and R1 may be covered by the sealed task approval. R2 and above require an
exact approval unless the Runner is executing a previously registered recovery
obligation. The approval binds:

- run and risk class;
- the canonical SHA-256 of tool ID plus immutable arguments;
- an active decision;
- a decision time before the Runner's trusted authorization time;
- an optional expiration later than that authorization time.

The Runner supplies the authorization clock. A timestamp supplied by the
request cannot extend an approval. Changing any request argument changes its
digest and invalidates the approval.

## Budgets and rollback

The snapshot records cumulative tool calls, requested wall time, experiments,
patch files, patch lines and output bytes. Reservations happen before dispatch,
so a crash cannot make consumed capacity disappear. Requests cannot raise a
limit. Rollback is budget-exempt only when its intervention ID is already an
active obligation; this prevents an exhausted run from losing its recovery
path.

An `apply_patch` authorization registers its intervention ID before mutation.
The obligation survives checkpoints and process restart, and is removed only
after the trusted recovery adapter reports successful baseline restoration.
Failure to verify restoration results in `ROLLBACK_REQUIRED`.

## Audit model

Every proposed tool request produces a `ToolCall` record with the exact request
digest, risk, policy decision and lifecycle status. `DENY` maps to `DENIED`,
`REQUIRE_APPROVAL` maps to `APPROVAL_PENDING`, and only `ALLOW` can proceed to
`RUNNING` and a terminal execution status. Unknown tool identifiers are retained
as risk `UNKNOWN` only in denied audit records.

The controller ledger separately records authorization, intent, completion and
failure ordering. ToolCall records explain what was authorized; the hash-chained
ledger explains when controller state changed.

When a file audit store is configured, the complete ToolCall list is sealed by
a canonical digest and atomically replaced at each lifecycle change. `RUNNING`
is persisted before dispatch. A single-writer restart converts any surviving
`RUNNING` record to `FAILED/INTERRUPTED_BEFORE_COMPLETION` before controller
recovery, so request IDs remain unique and an interrupted call is never reported
as successful.

## Trusted computing base and current limit

`PolicyEngine`, `GuardedExecutionAdapter`, checkpoint storage and concrete
execution adapters are Runner-owned trusted code. The model cannot replace
them. Phase 1A proves policy, audit, budget and crash/recovery behavior using a
simulated adapter. It does not yet prove that a hostile OS process or Gradle
plugin is contained. WP6 supplies that isolation harness; Phase 1B supplies the
real Gradle, ADB, Macrobenchmark and Perfetto adapters.

## Executable evidence

The implementation lives under `src/causalperf_agent/policy/`. Tests cover
unknown tools, unsafe shells and substitutions, path traversal, protected-path
mutation, exact device/package scope, immutable requests, exact and timed
approvals, budget exhaustion, contract-valid audit records, side-effect denial,
and durable audit plus rollback-obligation recovery after an injected crash.
