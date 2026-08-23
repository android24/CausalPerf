# Isolation backend conformance

Contract tests and host conformance are different claims. A backend may be
implemented and unit-tested without being approved to produce benchmark scores
on a particular machine.

## States

| State | Meaning | May publish a score? |
|---|---|---:|
| `CONTRACT_TESTED` | configuration, path, lifecycle and failure behavior pass deterministic tests | no |
| `HOST_PROBED` | the real OS backend starts and basic denial checks pass on one host | no |
| `HOST_CONFORMANT` | the complete checklist below passes for the exact host image | yes |
| `UNSUPPORTED` | a required control is absent or cannot be proved | no |

## Current evidence

| Backend | Contract tests | Host evidence | Current ceiling |
|---|---:|---|---|
| Linux Bubblewrap | PASS | scoring-host probe pending | `CONTRACT_TESTED` |
| macOS sandbox-exec | PASS | network denial and CPU-001 private-read denial probed on 2026-08-22 | `HOST_PROBED` |
| Windows Sandbox | PASS | Windows 11 24H2 `wsb.exe` host probe pending | `CONTRACT_TESTED` |

GitHub Actions runs the contract suite on Linux and macOS. Hosted CI does not
qualify the isolation security of a scoring host and does not replace this
checklist.

## Mandatory scoring-host checklist

Run separately for Agent and evaluator views and retain sealed logs outside the
public result:

1. record OS/build, backend executable digest, runtime paths and policy digest;
2. prove backend probe and role-specific launch succeed without fallback;
3. prove public input and required runtime files are readable;
4. prove the Agent cannot read the private evaluator path or private canaries;
5. prove only the evaluator view can read private Ground Truth;
6. prove outbound, public-internet and loopback network attempts are denied;
7. prove protected workspace paths cannot be persisted to the host;
8. prove only declared writable subtrees and role outputs are persisted;
9. prove inherited credentials and non-allowlisted environment keys are absent;
10. prove timeout and output-limit termination remove the exact owned process
    group or Sandbox UUID;
11. prove post-workspace, output, log and evaluator scans detect seeded canaries;
12. validate and seal the resulting Isolation Report.

Any missing or ambiguous observation is `UNSUPPORTED`, not a partial pass.
Changing the OS image, backend executable, virtualization configuration or
mapped runtime roots invalidates prior host conformance.

## Platform requirements

- Linux requires a working Bubblewrap installation with user/mount/PID/network
  namespace creation available to the runner.
- macOS requires `/usr/bin/sandbox-exec` and host-sensitive denied roots that
  contain the private evaluator while excluding required runtime roots.
- Windows requires Windows 11 24H2 or later, hardware virtualization, the
  `Containers-DisposableClientVM` feature and the UUID-addressable `wsb.exe`
  lifecycle CLI. Legacy `WindowsSandbox.exe` launch-only hosts are unsupported.
