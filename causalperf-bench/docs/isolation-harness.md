# Evaluation isolation harness

The WP6 harness runs untrusted Agent code and the private evaluator as two
sequential sandboxed processes over different filesystem views. It fails closed
when the requested backend is missing, cannot apply its policy, exposes a
private path, or cannot complete leakage scans.

## Sealed inputs and output

```text
IsolationPolicy
PrivateCanarySet
IsolationRun
    -> isolated Agent process
    -> post-Agent scan and protected-path verification
    -> isolated evaluator process
    -> post-evaluator publication scan
    -> IsolationReport
```

All four documents are versioned JSON Schema artifacts with canonical SHA-256
digests. `IsolationRun` binds the exact policy and private canary-set digests.
Changing a command, path, environment value, policy or canary set invalidates
the run specification.

## View separation

The authoring public tree, private evaluator tree and run root must be physically
disjoint. The harness exports a fresh public tree, makes every directory and
file read-only, and selectively restores owner write permission only to declared
Agent paths. Protected paths are hashed before and after Agent execution.

The Agent process receives:

- the exported public workspace;
- declared writable subtrees;
- a dedicated Agent output directory;
- explicitly allowlisted runtime files and environment keys.

It does not receive the private evaluator directory, evaluator output, evaluator
logs, inherited host environment or network access. After the Agent terminates,
the evaluator receives the sealed Agent view/output/logs plus the private
evaluator tree. Its only writable location is a private evaluator-output tree.
Only the bounded `IsolationReport` is returned by the harness.

## Backends

### Linux Bubblewrap

The preferred evaluation backend uses `bwrap --unshare-all`, a new session,
PID/mount/user/network namespaces, an empty environment, read-only binds for
runtime and input views, and writable binds only for declared outputs. Failure
to create the namespaces returns `UNSUPPORTED`; the command is never rerun
without isolation.

### macOS sandbox-exec

The Darwin backend denies network, all filesystem writes, undeclared process
execution and signals to other processes. It denies reads under policy-declared
host-sensitive roots, then precisely re-allows runtime and role-specific input
views. This ordering is necessary because pure `deny default` prevents common
Python/Gradle runtimes from initializing on current macOS.

Every private evaluator root must be contained by a denied host root. `/` is
not accepted as a denied root, and a runtime root that overlaps the private
evaluator is rejected. Evaluation deployments should deny all user, workspace,
temporary and credential-bearing roots, then explicitly list only required
SDK/JDK/runtime paths. Linux namespaces remain preferred for published runs.

### Windows Sandbox

The Windows backend requires Windows 11 24H2 or later, the optional
`Containers-DisposableClientVM` feature, hardware virtualization and the
`wsb.exe` lifecycle CLI. It creates a new UUID-addressed Windows Sandbox for
each role. Agent and evaluator therefore run in separate disposable VMs, and a
timeout invokes `wsb stop --id` for that exact instance. Older hosts that only
offer `WindowsSandbox.exe` fail closed because they do not provide the owned
instance lifecycle required by this harness.

The generated configuration disables networking, vGPU, clipboard, audio,
video and printer redirection and enables Protected Client. Role inputs,
runtime directories and the bootstrap are mapped read-only. When the Agent has
a writable subtree inside its workspace, the complete workspace is copied to
VM-local storage; after execution, `robocopy /MIR` synchronizes only each
declared writable subtree into a separately mapped host destination. The
private evaluator is never present in the Agent VM. Agent output is a separate
writable mapping, and evaluator output is writable only in the evaluator VM.

Windows Policy, Run and Report contracts are v2. They admit drive-letter
absolute paths and the `WINDOWS_SANDBOX` backend. Sealed v1 POSIX contracts are
validated against their archived schemas and migrated without information
loss. Non-system executables must reside under a declared runtime read
directory because Windows Sandbox maps folders, not individual files.

## Environment, process and resource controls

- The harness constructs child environments only from caller-provided keys that
  also occur in the role-specific policy allowlist; it never copies the host
  environment.
- The Windows bootstrap additionally injects only fixed VM-local operating
  system values (`SystemRoot`, `SystemDrive`, `TEMP`, `TMP`) needed to run child
  processes; caller values cannot override them.
- Agent arguments and environment values may not reveal the private evaluator
  path and are scanned for private canaries before launch.
- POSIX children start in a new process group. Timeout cleanup signals only
  that owned group, escalating from `SIGTERM` to `SIGKILL`; Windows cleanup
  stops the exact UUID-addressed Sandbox and then terminates its owned launcher
  tree if necessary.
- Wall time is enforced by the parent; file output is bounded by `RLIMIT_FSIZE`
  and checked again across stdout/stderr.
- Executables are absolute and allowlisted. Shell fallback is not used.

## Leakage scans

The scanner rejects private canary values and prefixes, Ground Truth markers,
private filenames, VCS/build-cache paths, symlinks and scan-budget exhaustion.
It runs over:

1. public input and both structured commands;
2. Agent and evaluator environments;
3. post-Agent workspace;
4. Agent output;
5. Agent stdout/stderr logs, including prompts and ledgers placed there;
6. evaluator output and logs before publication.

A finding records only a bounded code, counts and artifact digests. It never
copies the canary or private answer into the public report. Any finding produces
`LEAK_DETECTED` and prevents acceptance.

## Backend conformance evidence

On 2026-08-22 the Darwin OS backend was exercised outside the enclosing Codex
sandbox. The probe established that `sandbox-exec` starts successfully, rejects
local-loopback network access, permits the CPU-001 public view, and returns
`Operation not permitted` for CPU-001 private Ground Truth under the same
host-root-deny/public-view-allow policy. This is implementation evidence, not a
qualification result.

Every evaluation host must rerun backend probing. A nested sandbox or CI host
that cannot apply the selected backend returns `UNSUPPORTED`; it cannot produce
a valid benchmark score.

## Implementation and tests

`tools/isolation/` contains the policy models, scanner, Darwin/Linux/Windows backends
and orchestrator. `tools/run_isolated_evaluation.py` consumes sealed policy,
canary and run documents and writes one sealed report. Tests inject canaries
into input, environment, Agent output, logs and evaluator output; mutate
protected paths; tamper with run bindings; exhaust output; fail and time out
both roles; and verify backend command/profile/configuration construction. The
Windows contract tests verify disabled host integrations, read-only input,
VM-local workspace staging, exact writable-subtree copyback, structured
argument invocation and environment clearing. A real Windows host conformance
run is still mandatory before that host may publish a benchmark score.
