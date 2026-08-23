# Security and execution boundaries

## Principles

Agent-generated commands, repository content, build scripts, traces, and task
metadata are untrusted. Least privilege applies to files, devices, commands,
credentials, network access, and time/resource budgets.

## Filesystem

- Resolve every writable path against the run workspace before mutation.
- Deny writes outside task-declared paths.
- Mount benchmark, correctness tests, policies, and evaluator inputs read-only
  or outside the agent sandbox.
- Private Ground Truth is never mounted in the agent environment.
- Record pre/post content digests and reject protected-path drift.
- Apply changes in an isolated worktree or disposable copy with a patch-based
  rollback path.

## Commands

- Execute structured command specifications, not model-generated shell strings.
- Allowlist executable plus argument families per runner.
- Disallow shell control operators, unresolved globs, command substitution, and
  destructive filesystem commands.
- Require explicit timeout, output limit, working directory, and environment.
- Network access is denied by default and enabled only by task policy.

## Android device

- Resolve an explicit ADB serial; never use an ambiguous default device.
- Restrict operations to the declared application package and approved capture
  commands.
- Destructive device actions, system partition changes, rooting, account
  changes, and factory reset are outside initial scope.
- Record package, APK digest, device fingerprint, and installation outcome.

## Credentials and privacy

- Do not persist API keys, signing secrets, raw device serials, user data, or
  private source in reports and ledgers.
- Use test accounts and synthetic benchmark data.
- Sanitize traces and logs before external model or service access.
- No artifact upload occurs without explicit policy and user authorization.

## Approval boundaries

Read-only inspection and benchmark execution within an approved task may be
automatic. Source mutation, dependency changes, network enablement, device-wide
changes, patch publication, and PR creation require separate approvals. See the
Agent approval model.

## Failure recovery

On interruption or failed gates:

1. stop starting new commands;
2. preserve immutable logs and artifacts;
3. terminate only processes owned by the run;
4. restore the source workspace from the recorded patch boundary;
5. restore or reinstall the declared baseline APK when needed;
6. record recovery status and unresolved residue;
7. return `ROLLBACK_REQUIRED` if restoration cannot be verified.

## Phase 1A enforcement status

`src/causalperf_agent/policy/` now enforces the semantic boundary before
adapter dispatch. It rejects unknown tools, unsafe commands, path traversal,
protected mutations, device/package/partition mismatch, stale or mismatched
approvals, and budget exhaustion. Budget use and rollback obligations are
checkpointed before side effects, and all requests produce ToolCall audit
records.

This does not replace process isolation. The execution adapter is part of the
trusted Runner, and WP6 must still prove network denial, sanitized environments,
separate Agent/evaluator views, process ownership and pre/post output scanning.
