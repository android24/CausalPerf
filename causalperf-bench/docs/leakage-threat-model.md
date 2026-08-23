# Benchmark leakage threat model

Directory naming is not a confidentiality boundary. A valid evaluation uses
separate exported artifacts and separate execution principals.

## Leakage channels

- Git objects, branches, tags, commit messages, and reflogs;
- private files, hidden tests, reference patches, and evaluator configuration;
- symlinks or path traversal outside the public package;
- build caches, temporary directories, CI artifacts, and environment variables;
- filenames, comments, trace labels, task IDs, and benchmark-specific constants;
- network access to the source repository or prior benchmark publications;
- model context that previously contained private evaluator material;
- timing or error-message side channels from hidden tests;
- benchmark-runner detection and task-specific fast paths.

## Required export pipeline

```text
Authoring repository
  -> build public package from allowlisted paths
  -> strip VCS and local caches
  -> reject escaping links and forbidden files
  -> generate file/digest manifest
  -> scan for evaluator canaries
  -> unpack into a fresh Agent sandbox
  -> run Agent with network denied and an environment allowlist
  -> seal Agent outputs and terminate Agent process
  -> mount outputs plus private evaluator in a separate evaluator process
```

## Canary strategy

Private evaluator packages contain randomly generated canary identifiers that
must never occur in Agent inputs, logs, prompts, patches, or ledgers. Canary
detection invalidates the run. Canaries supplement, not replace, access control.

## Public-task realism review

Before freeze, review public source for answer-signaling language, suspicious
names, benchmark-only branches, trace markers that directly state the cause,
and comments that identify the intended fix. Task-owned trace markers may
identify a region but must not encode the hidden causal label.

## Current enforcement

`validate_task.py` rejects known private filenames, embedded Git history,
unsafe relative paths, writable/protected overlap, and symlinks escaping the
public package. `export_public_task.py` creates a fresh read-only public tree,
rejects every symlink, scans private markers/canaries, and emits a file/digest
manifest.

WP6 adds a fail-closed [evaluation isolation harness](isolation-harness.md).
Linux Bubblewrap, macOS sandbox-exec and Windows Sandbox backends enforce
separate Agent and evaluator views, network denial,
executable/runtime/environment allowlists, write boundaries, owned process or
VM lifecycles, time/output limits and bounded public reports. Windows uses
read-only host inputs, VM-local workspace staging and exact writable-subtree
copyback so a broad writable mapped folder is never exposed. Seeded canaries
are scanned before execution and across the resulting workspace, output, logs
and evaluator publication. Unsupported hosts return `UNSUPPORTED`;
bare-process fallback is prohibited.
